#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$ROOT" == "$HOME/Logistics_OMX" ]] || { echo "[ERROR] expected ~/Logistics_OMX: $ROOT" >&2; exit 2; }
ID=""; BROKER="192.168.0.16"
while [[ $# -gt 0 ]]; do
 case "$1" in
 --robot-id) ID="${2:?}"; shift 2;;
 --broker-ip) BROKER="${2:?}"; shift 2;;
 --skip-deps) shift;;
 *) echo "[ERROR] unknown argument: $1" >&2; exit 2;;
 esac
done
case "$ID" in omx1|omx2|omx3|omx4) ;; *) echo '[ERROR] robot-id=omx1|omx2|omx3|omx4 required' >&2; exit 2;; esac
VENV="${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}"
[[ -x "$VENV/bin/python" && -f "$VENV/bin/activate" ]] || { echo "[ERROR] existing venv missing: $VENV" >&2; exit 1; }
SRC="$ROOT/lerobot_unified"
[[ -f "$SRC/src/lerobot/rollout/strategies/base.py" ]] || { echo '[ERROR] unified LeRobot source is missing' >&2; exit 1; }
"$VENV/bin/python" - <<'CHECK_INSTALLED'
from importlib.metadata import version
for name in ('lerobot','torch','paho-mqtt','dynamixel-sdk'):
 print(f'[VENV] {name}=={version(name)}')
if version('lerobot')!='0.6.2': raise SystemExit('[ERROR] production LeRobot version must be 0.6.2')
CHECK_INSTALLED
mkdir -p "$ROOT/state" "$HOME/.config/logistics_omx"
"$VENV/bin/python" -m pip freeze > "$ROOT/state/pip_existing_before_setup.txt"
# Preserve model weights in legacy directory. This creates only one new symlink.
if [[ ! -e "$SRC/outputs" && ! -L "$SRC/outputs" && -d "$HOME/il_ws/src/lerobot/outputs" ]]; then
 ln -s "$HOME/il_ws/src/lerobot/outputs" "$SRC/outputs"
fi
[[ -d "$SRC/outputs" ]] || echo '[WARN] unified outputs not linked; check model paths manually.' >&2
DEVICE="$HOME/.config/logistics_omx/device.env"
if [[ ! -f "$DEVICE" ]]; then
 printf 'OMX_ROBOT_ID=%q\nOMX_BROKER_IP=%q\nOMX_PYTHON_ENV_PATH=%q\n' "$ID" "$BROKER" "$VENV" > "$DEVICE"
else
 grep -Eq "^OMX_ROBOT_ID=$ID$" "$DEVICE" || { echo "[ERROR] device config ID mismatch: $DEVICE" >&2; exit 1; }
 grep -q '^OMX_PYTHON_ENV_PATH=' "$DEVICE" || printf 'OMX_PYTHON_ENV_PATH=%q\n' "$VENV" >> "$DEVICE"
fi
PROFILE="$HOME/.config/logistics_omx/profile.env"
if [[ ! -f "$PROFILE" ]]; then
 cp "$ROOT/robots_ws/src/omx_manipulator/runtime/profiles/$ID.env.example" "$PROFILE"
 echo "[PROFILE] Created $PROFILE; verify hardware and model paths before running."
else
 echo "[PROFILE] Preserved $PROFILE (compare to current $ID.env.example)."
fi
# Copy model mapping per device only if absent, no live existing JSON overwritten.
if [[ "$ID" == omx1 || "$ID" == omx2 ]]; then
 case "$ID" in omx1) NAME=omx_order_models.json;; omx2) NAME=omx_cd_order_models.json;; esac
 TARGET="$HOME/.config/logistics_omx/$NAME"
 [[ -f "$TARGET" ]] || { if [[ -f "$HOME/il_ws/src/lerobot/$NAME" ]]; then cp "$HOME/il_ws/src/lerobot/$NAME" "$TARGET"; else cp "$SRC/$NAME" "$TARGET"; fi; }
 if ! grep -q '^OMX_ORDER_MODELS_FILE=' "$PROFILE"; then printf 'OMX_ORDER_MODELS_FILE=%q\n' "$TARGET" >> "$PROFILE"; fi
fi
BASHRC="$HOME/.bashrc"
LINE='source "$HOME/Logistics_OMX/scripts/omx_shell.sh" # Logistics_OMX shell integration'
if ! grep -Fq 'Logistics_OMX shell integration' "$BASHRC" 2>/dev/null; then printf '\n%s\n' "$LINE" >> "$BASHRC"; fi
export PYTHONPATH="$SRC/src${PYTHONPATH:+:$PYTHONPATH}"
"$VENV/bin/python" - <<'CHECK_SOURCE'
import lerobot
print('[SOURCE]',lerobot.__file__)
CHECK_SOURCE
bash -n "$ROOT/run_omx_mqtt.sh" "$SRC/run_omx_backend.sh" "$SRC/run_e_rollout.sh" "$SRC/run_f_rollout.sh"
"$VENV/bin/python" -m py_compile "$SRC/src/lerobot/rollout/configs.py" "$SRC/src/lerobot/rollout/strategies/base.py" "$SRC/omx_mqtt_client.py"
echo "[OK] $ID unified source configured; legacy source/weights/venv untouched. DO NOT run robot without preflight."
