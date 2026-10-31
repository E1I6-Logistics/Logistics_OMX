"""Non-motor fixture tests. These do not certify live motor stop or MQTT behavior."""
import tempfile
from pathlib import Path
from apply_omx_fail_closed import FILES, patch, run

LAUNCH = '''#!/usr/bin/env bash
if [[ "$ROBOT_ID" == "omx1" || "$ROBOT_ID" == "omx2" ]]; then
  bash "$SCRIPT_DIR/omx_new_order.sh" WAIT 0
fi

"${OMX_RUN_PREFIX[@]}" python3 "$SCRIPT_DIR/omx_mqtt_client.py" \\
  --robot-id "$ROBOT_ID" &
'''
MQTT = '''import os
from pathlib import Path

def execute_orders(job_id, orders):
    while True:
            rollout_pid_text = os.environ.get("OMX_ROLLOUT_PID", "")
            if rollout_pid_text:
                break
            return

def run_job(job_id, orders):
    try:
        pass
    except Exception as exc:
        result_message = str(exc)

    finally:
        return
'''
CORE = '''import logging
import contextlib
from pathlib import Path

def send_next_action(ctx, interpolator):
    engine = ctx.policy.inference
    if interpolator.needs_new_action():
        pass
    action_dict = {}
    obs_raw = None
    section = contextlib.nullcontext
    with section("send"):
        processed = ctx.processors.robot_action_processor((action_dict, obs_raw))
        ctx.hardware.robot_wrapper.send_action(processed)
    return action_dict
'''
BASE = '''import os
from pathlib import Path

def run(ctx, interpolator, engine):
    while True:
        try:
            while not ctx.runtime.shutdown_event.is_set():
                timer.tick(new_cycle=interpolator.needs_new_action())
                break
        finally:
            pass
'''


def main():
    samples = dict(zip(FILES, (LAUNCH, MQTT, CORE, BASE)))
    for attempt in range(1, 6):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, contents in samples.items():
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(contents, encoding='utf8')
            # Check must not modify files.
            run(root, True)
            assert all((root / p).read_text(encoding='utf8') == samples[p] for p in FILES)
            run(root, False)
            modified = {p: (root / p).read_text(encoding='utf8') for p in FILES}
            assert 'export OMX_ROLLOUT_PID="$ROLLOUT_PID"' in modified[FILES[0]]
            assert 'Path(abort_path).touch(exist_ok=True)' in modified[FILES[1]]
            assert 'Pickup rollout PID missing' in modified[FILES[1]]
            assert modified[FILES[2]].count('abort_if_requested()') == 4
            assert 'OMX abort flag set' in modified[FILES[3]]
            try:
                patch(modified)
                raise AssertionError('double patch incorrectly allowed')
            except ValueError:
                pass
            assert all((root / (p + '.bak_fail_closed')).read_text(encoding='utf8') == samples[p] for p in FILES)
        print(f'[PASS] fixture round {attempt}/5: anchors, parse, backup, idempotence')
    print('[PASS] 5 synthetic fixture rounds; physical/MQTT integration NOT tested')


if __name__ == '__main__':
    main()
