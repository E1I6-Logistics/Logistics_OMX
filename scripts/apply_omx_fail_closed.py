#!/usr/bin/env python3
"""Apply a narrowly scoped fail-closed link to Logistics_OMX dd96e1d.
No motor actions; backup is written before any source modification.
"""
import argparse
import ast
from pathlib import Path
import subprocess
import sys

FILES = (
    'lerobot_unified/run_omx_backend.sh',
    'lerobot_unified/omx_mqtt_client.py',
    'lerobot_unified/src/lerobot/rollout/strategies/core.py',
    'lerobot_unified/src/lerobot/rollout/strategies/base.py',
)


def replace_one(src, old, new, title):
    n = src.count(old)
    if n != 1:
        raise ValueError(f'{title}: expected one anchor, found {n}: {old[:95]!r}')
    return src.replace(old, new, 1)


def patch(sources):
    launcher, mqtt, core, base = (sources[p] for p in FILES)
    launcher = replace_one(launcher,
        '  bash "$SCRIPT_DIR/omx_new_order.sh" WAIT 0\nfi',
        '''  # MQTT failure flag is unique to this launcher session. Never reuse an old flag.
  OMX_ABORT_FILE="$(mktemp "${TMPDIR:-/tmp}/${ROBOT_ID}_abort.XXXXXXXX")"
  rm -f -- "$OMX_ABORT_FILE"
  export OMX_ABORT_FILE
  bash "$SCRIPT_DIR/omx_new_order.sh" WAIT 0
fi''', 'launcher abort flag')
    launcher = replace_one(launcher,
        '"${OMX_RUN_PREFIX[@]}" python3 "$SCRIPT_DIR/omx_mqtt_client.py" \\\n',
        '''# Pass the rollout PID to the MQTT child. Workstation PID remains session-managed.
export OMX_ROLLOUT_PID="$ROLLOUT_PID"
"${OMX_RUN_PREFIX[@]}" python3 "$SCRIPT_DIR/omx_mqtt_client.py" \\
''', 'launcher pid export')
    # Failure flag write is best-effort, logged, and precedes MQTT ERROR publication.
    mqtt = replace_one(mqtt, 'import argparse\n', 'import argparse\n', 'mqtt import sanity') if False else mqtt
    mqtt = replace_one(mqtt,
        '        result_message = str(exc)\n\n    finally:',
        '''        result_message = str(exc)
        if robot_id in PICKUP_ITEMS:
            abort_path = os.environ.get("OMX_ABORT_FILE", "")
            if abort_path:
                try:
                    # Flag creation is atomic at the filesystem level; no motor commands here.
                    Path(abort_path).touch(exist_ok=True)
                except OSError:
                    print("[SAFETY] Failed to signal rollout abort", flush=True)

    finally:''', 'mqtt error signal')
    # Extend existing pid guard: a bad / inaccessible pid is also error, rather than silent.
    mqtt = replace_one(mqtt,
        '            rollout_pid_text = os.environ.get("OMX_ROLLOUT_PID", "")\n',
        '''            rollout_pid_text = os.environ.get("OMX_ROLLOUT_PID", "")
            if not rollout_pid_text:
                raise RuntimeError("Pickup rollout PID missing; refusing unmonitored order")
''', 'mqtt pid required')
    core = replace_one(core, 'import logging\n', 'import logging\nimport os\nfrom pathlib import Path\n', 'core imports')
    core = replace_one(core,
        '    if interpolator.needs_new_action():\n',
        '''    # Abort flag is only set for launcher-managed OMX1/2 sessions.
    # The guard is repeated immediately before send_action because inference can block.
    abort_path = os.environ.get("OMX_ABORT_FILE", "")
    def abort_if_requested():
        if abort_path and Path(abort_path).exists():
            interpolator.reset()
            engine.reset()
            raise RuntimeError("OMX abort signalled; no more motor actions dispatched")

    abort_if_requested()
    if interpolator.needs_new_action():
''', 'core pre-inference gate')
    core = replace_one(core,
        '    with section("send"):\n        processed = ctx.processors.robot_action_processor((action_dict, obs_raw))\n        ctx.hardware.robot_wrapper.send_action(processed)',
        '''    abort_if_requested()
    with section("send"):
        processed = ctx.processors.robot_action_processor((action_dict, obs_raw))
        abort_if_requested()
        ctx.hardware.robot_wrapper.send_action(processed)''', 'core pre-send gate')
    base = replace_one(base,
        '            while not ctx.runtime.shutdown_event.is_set():\n                timer.tick(',
        '''            while not ctx.runtime.shutdown_event.is_set():
                abort_path = os.environ.get("OMX_ABORT_FILE", "")
                if abort_path and Path(abort_path).exists():
                    interpolator.reset()
                    engine.reset()
                    raise RuntimeError("OMX abort flag set; control loop terminated")
                timer.tick(''', 'base control-loop gate')
    out = dict(zip(FILES, (launcher, mqtt, core, base)))
    for path in FILES:
        if path.endswith('.py'):
            ast.parse(out[path], filename=path)
    return out


def run(root, check):
    root = Path(root)
    source = {p: (root / p).read_text(encoding='utf-8') for p in FILES}
    modified = patch(source)
    if check:
        print('[PASS] 4 files matched, patch compiled, no source changes')
        return
    originals = {p: root / (p + '.bak_fail_closed') for p in FILES}
    existing = [str(v) for v in originals.values() if v.exists()]
    if existing:
        raise ValueError(f'backup already exists; no changes: {existing}')
    for p in FILES:
        originals[p].write_bytes((root / p).read_bytes())
    for p in FILES:
        (root / p).write_text(modified[p], encoding='utf-8', newline='\n')
    print('[PASS] fail-closed patch applied to 4 files, backups preserved')


if __name__ == '__main__':
    cli = argparse.ArgumentParser()
    cli.add_argument('--check', action='store_true')
    cli.add_argument('--root', default='.')
    args = cli.parse_args()
    try:
        run(args.root, args.check)
    except (OSError, ValueError, SyntaxError) as e:
        sys.exit(f'[FAIL] {e}')
