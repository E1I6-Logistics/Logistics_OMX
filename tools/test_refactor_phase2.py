#!/usr/bin/env python3
"""Offline source-transform regression tests; fake code only; no motors/MQTT."""
import argparse
import copy
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

import refactor_phase2 as m


def fixture():
    return {
        m.FACTORY: '''from .act_prefetch import create_omx_sync_engine
class SyncInferenceConfig: pass
class RTCInferenceConfig: pass
def create_inference_engine(config):
    if isinstance(config, SyncInferenceConfig):
        return create_omx_sync_engine(
            policy=None)
    if isinstance(config, RTCInferenceConfig):
        return RTCInferenceEngine(
            policy=None)
''',
        m.ROUTER: '''from .inference.sync import SyncInferenceEngine
from .inference.act_prefetch import create_omx_sync_engine
class OrderModelRouter:
    def select(self):
        if 1:
            raise ValueError("Only ACT models are supported")
        engine = create_omx_sync_engine(
            policy=None)
''',
        m.LAUNCHER: '''#!/bin/bash
OMX_PREFETCH_ENV_FILE="${OMX_PREFETCH_ENV_FILE:-$HOME/.config/omx/act_prefetch.env}"
if [[ -f "$OMX_PREFETCH_ENV_FILE" ]]; then
  source "$OMX_PREFETCH_ENV_FILE"
fi
source "$SCRIPT_DIR/omx_runtime_env.sh"
ROLLOUT_SCRIPT="$SCRIPT_DIR/run_e_rollout.sh"
exec lerobot-rollout --strategy.order_models_file=foo
"$SCRIPT_DIR/omx_mqtt_client.py"
''',
        m.SETTINGS: '''#!/bin/bash
# Local Raspberry Pi settings, no model/checkpoint modification.
case "$1" in
  on)
    export OMX_ACT_PREFETCH=1
    ;;
  off)
    export OMX_CPU_THREADS=4
    echo "[OK] OMX optimization disabled for NEXT launcher start: $FILE"
    ;;
esac
''',
        m.README: '# original project documentation\n',
    }


class Phase2Tests(unittest.TestCase):
    def test_end_to_end(self):
        result = m.transform(fixture())
        m.validate(result, deleted=False)
        self.assertEqual(len(result), 5)

    def test_existing_cpu_runtime_kept(self):
        output = m.transform(fixture())
        self.assertIn('source "$OMX_PREFETCH_ENV_FILE"', output[m.LAUNCHER])
        self.assertIn('source "$SCRIPT_DIR/omx_runtime_env.sh"', output[m.LAUNCHER])
        self.assertIn('export OMX_ACT_PREFETCH=0', output[m.LAUNCHER])

    def test_future_prefetch_enable_rejected(self):
        output = m.transform(fixture())
        on_case = output[m.SETTINGS].split('  on)\n', 1)[1].split('  off)\n', 1)[0]
        self.assertIn('exit 2', on_case)
        self.assertNotIn('export OMX_ACT_PREFETCH=1', on_case)

    def test_original_act_sync_constructor_kept(self):
        output = m.transform(fixture())
        self.assertIn('return SyncInferenceEngine(', output[m.FACTORY])
        self.assertIn('engine = SyncInferenceEngine(', output[m.ROUTER])
        self.assertIn('RTCInferenceEngine', output[m.FACTORY])

    def test_broken_anchor_fails_closed(self):
        f = fixture()
        f[m.ROUTER] = f[m.ROUTER].replace('engine = create_omx_sync_engine(', 'engine = NoLongerHere(')
        with self.assertRaisesRegex(ValueError, 'router sync constructor'):
            m.transform(f)

    def test_reapplying_fails_closed(self):
        output = m.transform(fixture())
        with self.assertRaises(ValueError):
            m.transform(output)

    def test_does_not_change_unrelated_source(self):
        original = fixture()
        output = m.transform(copy.deepcopy(original))
        self.assertEqual(original[m.LAUNCHER].split('exec lerobot-rollout')[1],
                         output[m.LAUNCHER].split('exec lerobot-rollout')[1])

    def test_full_filesystem_application(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, body in fixture().items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding='utf-8')
            for name in m.TO_DELETE:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'legacy prefetch')
            untouched = root / 'lerobot_unified/omx_mqtt_client.py'
            untouched.parent.mkdir(parents=True, exist_ok=True)
            untouched.write_bytes(b'preserve exactly')
            with patch.object(m, 'ROOT', root), patch.object(m, 'ensure_start', lambda: None):
                with redirect_stdout(StringIO()):
                    m.apply()
                    m.verify()
            self.assertEqual(untouched.read_bytes(), b'preserve exactly')
            self.assertFalse(any((root / p).exists() for p in m.TO_DELETE))

    def test_missing_delete_target_fails_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = fixture()
            for name, body in baseline.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding='utf-8')
            # Deliberately absent TO_DELETE paths -> apply must not modify source at all.
            with patch.object(m, 'ROOT', root), patch.object(m, 'ensure_start', lambda: None):
                with self.assertRaises(FileNotFoundError):
                    m.apply()
            for name, body in baseline.items():
                self.assertEqual((root / name).read_text(encoding='utf-8'), body)

    def test_mid_delete_error_restores_all_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = fixture()
            for name, body in baseline.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding='utf-8')
            for name in m.TO_DELETE:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'pre-existing legacy file')
            orig_unlink = Path.unlink
            inject_at = root / m.TO_DELETE[1]
            def injected_unlink(path, *args, **kwargs):
                if path == inject_at:
                    raise PermissionError('injected filesystem denial')
                return orig_unlink(path, *args, **kwargs)
            with patch.object(m, 'ROOT', root), patch.object(m, 'ensure_start', lambda: None):
                with patch.object(Path, 'unlink', injected_unlink):
                    with self.assertRaises(PermissionError):
                        m.apply()
            for name, body in baseline.items():
                self.assertEqual((root / name).read_text(encoding='utf-8'), body)
            for name in m.TO_DELETE:
                self.assertEqual((root / name).read_bytes(), b'pre-existing legacy file')

    def test_invalid_result_rejected(self):
        output = m.transform(fixture())
        output[m.LAUNCHER] = output[m.LAUNCHER].replace('export OMX_ACT_PREFETCH=0', '')
        with self.assertRaisesRegex(ValueError, 'launcher invariant'):
            m.validate(output, deleted=False)


if __name__ == '__main__':
    arg = argparse.ArgumentParser()
    arg.add_argument('--runs', type=int, default=10)
    opts = arg.parse_args()
    if not 1 <= opts.runs <= 50:
        arg.error('--runs must be 1..50')
    for i in range(opts.runs):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Phase2Tests)
        outcome = unittest.TextTestRunner(verbosity=0).run(suite)
        if not outcome.wasSuccessful():
            raise SystemExit(1)
        print(f'[PASS] round {i+1}/{opts.runs}: {outcome.testsRun} checks')
    print(f'[PASS] {opts.runs*11} offline scenario checks (synthetic sources, not robotics hardware)')
