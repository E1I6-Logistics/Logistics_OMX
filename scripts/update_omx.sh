#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$ROOT/lerobot_unified/src/lerobot/rollout/configs.py" ]] || { echo '[ERROR] unified source missing' >&2; exit 1; }
if pgrep -af '[l]erobot-rollout|[o]mx_mqtt_client.py' >/dev/null 2>&1; then
 echo '[ERROR] live rollout/MQTT process detected; stop before update' >&2; exit 1
fi
[[ -f "$HOME/.config/logistics_omx/device.env" ]] || { echo '[ERROR] install first' >&2; exit 1; }
source "$HOME/.config/logistics_omx/device.env"
bash "$ROOT/scripts/install_omx.sh" --robot-id "$OMX_ROBOT_ID" --broker-ip "${OMX_BROKER_IP:-192.168.0.16}"
