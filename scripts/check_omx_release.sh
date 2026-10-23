#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
echo '[CHECK] Syntax (no hardware, no MQTT messages)'
for f in run_omx_mqtt.sh lerobot_unified/*.sh scripts/*.sh robots_ws/src/omx_manipulator/runtime/run_omx_*.sh; do
  bash -n "$f" || exit 1
  echo "  OK $f"
done
python3 - <<'PY'
import ast
from pathlib import Path
files = [
 'lerobot_unified/src/lerobot/rollout/configs.py',
 'lerobot_unified/src/lerobot/rollout/strategies/base.py',
 'lerobot_unified/src/lerobot/rollout/workstation_ipc.py',
 'lerobot_unified/omx_mqtt_client.py',
]
for name in files:
 ast.parse(Path(name).read_text(encoding='utf-8'), filename=name)
 print('  OK', name)
PY
if [[ ! -f lerobot_unified/src/lerobot/datasets/lerobot_dataset.py ]]; then
  echo '[WARN] Old exported bundle lacks LeRobot datasets. Run scripts/complete_unified_source.sh on Pi, or deploy over a current GitHub checkout.'
fi
