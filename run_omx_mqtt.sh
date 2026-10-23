#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEVICE="$HOME/.config/logistics_omx/device.env"
if [[ -f "$DEVICE" ]]; then set -a; source "$DEVICE"; set +a; fi
ROBOT_ID="${OMX_ROBOT_ID:-}"; BROKER_IP="${OMX_BROKER_IP:-192.168.0.16}"
PROFILE="$HOME/.config/logistics_omx/profile.env"
if [[ -f "$PROFILE" ]]; then set -a; source "$PROFILE"; set +a; fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --robot-id) [[ $# -ge 2 ]] || exit 2; ROBOT_ID="$2"; shift 2 ;;
    --broker-ip) [[ $# -ge 2 ]] || exit 2; BROKER_IP="$2"; shift 2 ;;
    *) echo "[ERROR] 지원하지 않는 옵션: $1" >&2; exit 2 ;;
  esac
done
case "$ROBOT_ID" in
  omx1|omx2) ROLE="pickup" ;;
  omx3|omx4) ROLE="unload" ;;
  *) echo '[ERROR] omx1~4 지정 필요: scripts/install_omx.sh --robot-id omxN' >&2; exit 2 ;;
esac
LEROBOT_HOME="$ROOT/lerobot_unified"
[[ -d "$LEROBOT_HOME/src/lerobot" ]] || { echo "[ERROR] LeRobot 없음: $LEROBOT_HOME" >&2; exit 1; }
export OMX_LEROBOT_HOME="$LEROBOT_HOME"
if [[ "$ROLE" == pickup ]]; then
  export OMX_INVENTORY_STATE_PATH="${OMX_INVENTORY_STATE_PATH:-$ROOT/state/omx_inventory.json}"
  export OMX_MODEL_CONFIG_DIR="${OMX_MODEL_CONFIG_DIR:-$ROOT/state}"
  mkdir -p "$ROOT/state"
fi
exec "$ROOT/robots_ws/src/omx_manipulator/runtime/run_omx_${ROLE}.sh" --robot-id "$ROBOT_ID" --broker-ip "$BROKER_IP"
