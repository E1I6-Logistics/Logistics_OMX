"""Offline protocol and control-loop simulation; no real LeRobot, cameras or motors."""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import logging
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
IPC_PATH = ROOT / 'lerobot_unified/src/lerobot/rollout/workstation_ipc.py'
spec = importlib.util.spec_from_file_location('test_workstation_ipc', IPC_PATH)
ipc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc)


class FakeTimer:
    def __init__(self, *args, **kwargs):
        self.ticks = 0
    def restart(self): pass
    def tick(self, *args, **kwargs): self.ticks += 1
    def section(self, *args): return contextlib.nullcontext()
    def wait(self): time.sleep(.01)
    def log_episode_summary(self, *args, **kwargs): pass
    def log_run_summary(self): pass


class Robot:
    def __init__(self): self.calls=0
    def get_observation(self):
        self.calls += 1
        # First call: startup HOME. A new job: depart then return.
        return {'j': 25.0 if self.calls in (2, 9) else 0.0}


def build_fake_strategy():
    source = (ROOT / 'lerobot_unified/src/lerobot/rollout/strategies/base.py').read_text()
    module = ast.parse(source)
    base = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'BaseStrategy')
    method = next(node for node in base.body if isinstance(node, ast.FunctionDef) and node.name == '_run_persistent_workstation')
    ns = {'os':os, 'Path':Path, 'time':time, 'CycleTimer':FakeTimer,
          'atomic_json':ipc.atomic_json, 'new_status':ipc.new_status,
          'read_json':ipc.read_json, 'logger':logging.getLogger('mock'),
          'send_next_action':lambda obs, raw, ctx, interp, timer: ctx.sent.append(1),
          'RolloutContext':object}
    m = ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[]))
    exec(compile(m,'<workstation method>', 'exec'),ns)
    class Fake:
        _run_persistent_workstation = ns['_run_persistent_workstation']
        def __init__(self, command_path, status_path):
            self.config = SimpleNamespace(workstation_status_file=str(status_path),
                                          workstation_control_file=str(command_path),
                                          workstation_poll_s=.01, home_tolerance=7,
                                          home_leave_tolerance=12,
                                          idle_complete_joint_delta=.6,
                                          idle_complete_hold_s=.08)
            self._engine = SimpleNamespace(resume=lambda:None, pump_query=lambda *_:None)
            self._interpolator = SimpleNamespace(needs_new_action=lambda:True, multiplier=1)
            self.resets = 0
        def reset_control_state(self): self.resets+=1
        def _get_observation_with_camera_recovery(self, robot, engine): return robot.get_observation(), False
        def _max_home_error(self, obs, home):return abs(obs['j'] - home['j'])
        def _process_observation_and_notify(self, processors, obs): return obs
        def _handle_warmup(self, flag,timer): return False
        def _log_telemetry(self, *args):pass
    return Fake


def wait_until(condition, timeout=2):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if condition(): return True
        time.sleep(.01)
    return False


class TestProtocol(unittest.TestCase):
    def test_atomic_status_and_wrong_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'s.json'
            self.assertEqual(ipc.read_json(path),{})
            ipc.atomic_json(path,ipc.new_status('run-1','READY'))
            self.assertTrue(ipc.valid_ready(ipc.read_json(path),'run-1'))
            self.assertFalse(ipc.valid_ready(ipc.read_json(path),'old-run'))

    def test_no_motion_before_start_and_reuse_after_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            status=Path(tmp)/'status.json'; command=Path(tmp)/'command.json'
            os.environ['OMX_WORKSTATION_SESSION']='test-session'
            strategy=build_fake_strategy()(command, status)
            robot=Robot(); stop=threading.Event()
            ctx=SimpleNamespace(runtime=SimpleNamespace(cfg=SimpleNamespace(fps=30,use_torch_compile=False),
                                                         shutdown_event=stop,cadence_report=None),
                                hardware=SimpleNamespace(robot_wrapper=robot,initial_position={'j':0.0}),
                                processors=object(),sent=[])
            thread=threading.Thread(target=strategy._run_persistent_workstation,args=(ctx,),daemon=True)
            thread.start()
            self.assertTrue(wait_until(lambda:ipc.read_json(status).get('phase')=='READY'))
            self.assertEqual(len(ctx.sent),0)
            self.assertEqual(robot.calls,1)
            ipc.atomic_json(command,{'session':'old','command':'START','job_id':'old'})
            time.sleep(.06)
            self.assertEqual(len(ctx.sent),0)
            self.assertEqual(ipc.read_json(status)['phase'],'READY')
            ipc.atomic_json(command,{'session':'test-session','command':'START','job_id':'one'})
            self.assertTrue(wait_until(lambda:ipc.read_json(status).get('phase')=='DONE',3))
            self.assertGreater(len(ctx.sent),0)
            count=len(ctx.sent)
            time.sleep(.06)
            self.assertEqual(len(ctx.sent),count)
            self.assertEqual(ipc.read_json(status)['job_id'],'one')
            ipc.atomic_json(command,{'session':'test-session','command':'CLEAR','job_id':'one'})
            self.assertTrue(wait_until(lambda:ipc.read_json(status).get('phase')=='READY'))
            self.assertEqual(len(ctx.sent),count)
            # Correct second job; synthetic robot produces home-only after call 9,
            # so verify it starts and remains in RUNNING rather than stale-DONE.
            ipc.atomic_json(command,{'session':'test-session','command':'START','job_id':'two'})
            self.assertTrue(wait_until(lambda:ipc.read_json(status).get('job_id')=='two'))
            self.assertEqual(ipc.read_json(status)['phase'],'RUNNING')
            stop.set(); thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

if __name__=='__main__':unittest.main()
