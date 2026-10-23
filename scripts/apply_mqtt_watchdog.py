from pathlib import Path
import ast, argparse, re
p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args()
target=Path('lerobot_unified/omx_mqtt_client.py')
s=target.read_text(encoding='utf-8')
marker='def execute_orders(job_id, orders):'
start=s.index(marker); end=s.index('\ndef execute_unload(',start)
section=s[start:end]
if 'OMX_PICKUP_ORDER_TIMEOUT_S' in section: raise SystemExit('[FAIL] Already patched; no changes made')
anchor='    for index, (item, qty) in enumerate(orders, 1):'
assert section.count(anchor)==1
section=section.replace(anchor,'''    # Monitoring timeout only; do not confuse this with motor stop.
    per_item_timeout = float(os.environ.get("OMX_PICKUP_ORDER_TIMEOUT_S", "240"))
    if not 0 < per_item_timeout <= 3600:
        raise ValueError("OMX_PICKUP_ORDER_TIMEOUT_S must be within (0,3600]")

'''+anchor,1)
anchor='        last_progress = None'
assert section.count(anchor)==1
section=section.replace(anchor,'''        deadline = time.monotonic() + per_item_timeout
'''+anchor,1)
anchor='''        while True:

            state = json.loads(state_path.read_text())'''
assert section.count(anchor)==1
section=section.replace(anchor,'''        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Pickup order timeout: {item} {qty}, {per_item_timeout:.0f}s; motor stop NOT guaranteed")

            # Launcher may stop the rollout while MQTT is still alive.  Fail closed
            # if an explicitly supplied pid is no longer running.
            rollout_pid_text = os.environ.get("OMX_ROLLOUT_PID", "")
            if rollout_pid_text:
                try:
                    rollout_pid = int(rollout_pid_text)
                    if rollout_pid <= 0:
                        raise ValueError("invalid pid")
                    os.kill(rollout_pid, 0)
                except ProcessLookupError as exc:
                    raise RuntimeError("Pickup rollout process exited") from exc
                except (ValueError, PermissionError) as exc:
                    raise RuntimeError("Pickup rollout pid invalid/inaccessible") from exc

            state = json.loads(state_path.read_text())''',1)
s=s[:start]+section+s[end:]
# Safety guard from user's previous local patch must exist.
if 'if current_state == "ERROR":' not in s or 'if success and (robot_id not in WORKSTATION_IDS or workstation_ready())' not in s:
    raise SystemExit('[FAIL] Prior ERROR guard not present; no files changed')
# validate no unguarded current-state race is added; static syntactic safety only
ast.parse(s)
if args.check:
    print('[PASS] watchdog anchors, prior guard and syntax; no files written')
else:
    target.write_text(s,encoding='utf-8',newline='\n')
    print('[PASS] monitoring watchdog applied; review git diff')
