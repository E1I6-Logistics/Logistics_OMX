#!/usr/bin/env python3
"""10x non-actuating verification of the ACT prefetch production source.

Loads the optimizer itself with lightweight stand-ins for LeRobot wrappers;
uses real torch tensors, real worker threads, and the real prefetch methods.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import threading
import time
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import torch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'lerobot_unified/src/lerobot/rollout/inference/act_prefetch.py'


class Processor:
    def __init__(self, scale=1):
        self.scale = scale
        self.calls = 0
        self.resets = 0

    def __call__(self, value):
        self.calls += 1
        return value * self.scale if isinstance(value, torch.Tensor) else value

    def reset(self):
        self.resets += 1


class Policy:
    def __init__(self, steps=12, sleep_s=0.003, fail=False, kind='act', temporal=None):
        self.config = types.SimpleNamespace(type=kind, n_action_steps=steps,
                                            temporal_ensemble_coeff=temporal)
        self.steps = steps
        self.sleep_s = sleep_s
        self.fail = fail
        self.calls = 0
        self.reset_calls = 0
        self.lock = threading.Lock()

    def supports_text_generation(self):
        return False

    def reset(self):
        self.reset_calls += 1

    def predict_action_chunk(self, obs):
        time.sleep(self.sleep_s)
        if self.fail:
            raise RuntimeError('injected model failure')
        with self.lock:
            self.calls += 1
            serial = self.calls
        return torch.stack([torch.tensor([serial, k], dtype=torch.float32)
                            for k in range(self.steps)]).unsqueeze(0)


class FakeSyncInferenceEngine:
    def __init__(self, **kwargs):
        self._policy = kwargs['policy']
        self._preprocessor = kwargs['preprocessor']
        self._postprocessor = kwargs['postprocessor']
        self._dataset_features = kwargs['dataset_features']
        self._ordered_action_keys = kwargs['ordered_action_keys']
        self._device = torch.device(kwargs.get('device') or 'cpu')
        self._robot_type = kwargs['robot_type']
        self._task_name = kwargs['task']
        self._change = False
        self.dispatched = []

    def _take_task(self):
        changed, self._change = self._change, False
        return self._task_name, changed

    def _set_dispatched_task(self, task):
        self.dispatched.append(task)

    def reset(self):
        self._policy.reset()
        self._preprocessor.reset()
        self._postprocessor.reset()

    def get_action(self, obs):
        return torch.tensor([1000, 1001])


def import_real_optimizer():
    # Only fake external LeRobot dependencies; actual optimizer class, worker,
    # scheduling, and failure logic execute without any changes.
    for name in ('omx_testpkg', 'omx_testpkg.sync', 'lerobot',
                 'lerobot.policies', 'lerobot.policies.utils'):
        sys.modules.pop(name, None)
    pkg = types.ModuleType('omx_testpkg')
    pkg.__path__ = [str(SOURCE.parent)]
    sys.modules[pkg.__name__] = pkg
    sync = types.ModuleType('omx_testpkg.sync')
    sync.SyncInferenceEngine = FakeSyncInferenceEngine
    sys.modules[sync.__name__] = sync
    u = types.ModuleType('lerobot.policies.utils')
    u.prepare_observation_for_inference = lambda obs, device, task, robot_type: obs
    u.make_robot_action = lambda action, features: {'a': action[0].item(), 'b': action[1].item()}
    sys.modules['lerobot'] = types.ModuleType('lerobot')
    sys.modules['lerobot.policies'] = types.ModuleType('lerobot.policies')
    sys.modules[u.__name__] = u
    spec = importlib.util.spec_from_file_location('omx_testpkg.act_prefetch', SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def kwargs(policy):
    return dict(policy=policy, preprocessor=Processor(), postprocessor=Processor(),
                dataset_features={}, ordered_action_keys=['a', 'b'], task='pick',
                device='cpu', robot_type='test')


def next_action(engine, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = engine.get_action({'robot_state': torch.zeros(1, 2)})
        if value is not None:
            return value
        time.sleep(0.001)
    raise AssertionError('No action produced within timeout')


def expect_error(f, kind, msg=None):
    try:
        f()
    except kind as exc:
        if msg:
            assert msg in str(exc), str(exc)
    else:
        raise AssertionError(f'{kind.__name__} was not raised')


def round_trip(m):
    with patch.dict(os.environ, {'OMX_ACT_PREFETCH': '1', 'OMX_ACT_PREFETCH_STEPS': '6',
                                 'OMX_ACT_PREFETCH_STALL_S': '1',
                                 'OMX_ACT_PREFETCH_MAX_AGE_S': '4'}):
        # 1: genuinely creates optimized engine with same policy object
        pol = Policy()
        eng = m.create_omx_sync_engine(**kwargs(pol))
        assert isinstance(eng, m.PrefetchACTInferenceEngine)
        assert eng._policy is pol
        eng.start()
        try:
            # 2: first request is nonblocking, starves instead of motor repeat
            initial = eng.get_action({'robot_state': torch.zeros(1, 2)})
            assert initial is None
            # 3: first chunk actions ordered, and postprocessor used
            first = next_action(eng)
            assert first.tolist() == [1., 0.], first
            # 4: second action is sequential and no sync forward on controller
            second = next_action(eng)
            assert second.tolist() == [1., 1.], second
            assert eng._postprocessor.calls == pol.steps
            # 5: lookahead prefetch requested while actions remain
            for i in range(2, 7):
                assert next_action(eng).tolist() == [1., float(i)]
            assert eng._requested_at is not None or eng._ready is not None or eng._inflight
            # 6: next batch goes in correct order (the policy is only called in worker)
            for i in range(7, pol.steps):
                assert next_action(eng).tolist() == [1., float(i)]
            assert next_action(eng).tolist() == [2., 0.]
            # 7: reset discards old actions, starts over and does not lose worker
            eng.reset()
            assert next_action(eng).tolist()[1] == 0.
            # 8: old prefetched chunk TTL drops it rather than sending it
            eng.reset()
            with eng._state_lock:
                eng._ready = m._Chunk(eng._epoch, [torch.tensor([999., 999.])],
                                       time.monotonic() - 100)
            assert eng.get_action({'robot_state': torch.zeros(1, 2)}) is None
            assert next_action(eng).tolist()[0] != 999.
            # 9: text/task change fail closed (no old-instruction action)
            eng._change = True
            expect_error(lambda: eng.get_action({'robot_state': torch.zeros(1, 2)}),
                         RuntimeError, 'task changed')
        finally:
            eng.stop()
        # 10: closed engine cannot serve actions
        expect_error(lambda: eng.get_action({'robot_state': torch.zeros(1, 2)}), RuntimeError)

        # 11: worker inference failures propagate to controller safely
        bad = m.create_omx_sync_engine(**kwargs(Policy(fail=True)))
        bad.start()
        try:
            bad.get_action({'robot_state': torch.zeros(1, 2)})
            limit = time.monotonic() + 2
            while True:
                try:
                    bad.get_action({'robot_state': torch.zeros(1, 2)})
                except RuntimeError as e:
                    assert 'inference failed' in str(e)
                    break
                assert time.monotonic() < limit
                time.sleep(0.002)
        finally:
            bad.stop()
        # 12: unsupported architectures, ensemble, and disabled flag all fallback
        assert type(m.create_omx_sync_engine(**kwargs(Policy(kind='diffusion')))) is FakeSyncInferenceEngine
        assert type(m.create_omx_sync_engine(**kwargs(Policy(temporal=0.01)))) is FakeSyncInferenceEngine
        with patch.dict(os.environ, {'OMX_ACT_PREFETCH': '0'}):
            assert type(m.create_omx_sync_engine(**kwargs(Policy()))) is FakeSyncInferenceEngine
        # 13: invalid config explicitly rejected, no accidental mode change
        with patch.dict(os.environ, {'OMX_ACT_PREFETCH': 'invalid'}):
            expect_error(lambda: m.create_omx_sync_engine(**kwargs(Policy())), ValueError)
        # 14: never starts a motor action or opens a broker; only tensors
        assert all('send_action' not in x for x in dir(eng))
        # 15: model can be replaced by a new checkpoint's fresh policy instance
        newer = Policy(steps=18)
        eng2 = m.create_omx_sync_engine(**kwargs(newer))
        assert eng2._steps == 18 and eng2._lookahead == 6
        eng2.start()
        try:
            assert next_action(eng2).tolist() == [1., 0.]
        finally:
            eng2.stop()

        # 16: forward pass overlaps an executing chunk; all 200 actions
        # remain in sequence at a simulated 30 Hz-like regular controller rate.
        with patch.dict(os.environ, {'OMX_ACT_PREFETCH_STEPS': '70'}):
            sim = m.create_omx_sync_engine(**kwargs(Policy(steps=100, sleep_s=0.08)))
        sim.start()
        try:
            first = next_action(sim)
            assert first.tolist() == [1., 0.]
            for idx in range(1, 200):
                time.sleep(0.0035)  # Accelerated control-time simulation, NOT a real robot Hz test
                action = sim.get_action({'robot_state': torch.zeros(1, 2)})
                assert action is not None, f'control gap at simulated action {idx}'
                assert action.tolist() == [float(idx // 100 + 1), float(idx % 100)]
        finally:
            sim.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        parser.error('--runs must be in [1,100]')
    m = import_real_optimizer()
    for i in range(args.runs):
        round_trip(m)
        print(f'[PASS] round {i+1}/{args.runs}: 16 scenarios (real worker/tensors, fake policy; no motor)')
    print(f'[PASS] {args.runs * 16} scenario checks; actual robotics hardware NOT tested')

if __name__ == '__main__':
    main()
