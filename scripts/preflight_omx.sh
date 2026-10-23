#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE="$HOME/.config/logistics_omx/device.env"
[[ -f "$DEVICE" ]] || { echo '[ERROR] setup device.env first' >&2; exit 1; }
source "$DEVICE"
PROFILE="$HOME/.config/logistics_omx/profile.env"
[[ -f "$PROFILE" ]] && source "$PROFILE"
SRC="$ROOT/lerobot_unified"
[[ -f "$SRC/src/lerobot/rollout/configs.py" ]] || { echo '[ERROR] unified source missing' >&2; exit 1; }
[[ -x "${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}/bin/python" ]] || { echo '[ERROR] existing Python missing' >&2; exit 1; }
[[ -d "$SRC/outputs" ]] || { echo '[WARN] model outputs not linked' >&2; }
printf '[PRECHECK] device=%s broker=%s venv=%s source=%s\n' "$OMX_ROBOT_ID" "${OMX_BROKER_IP:-192.168.0.16}" "${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}" "$SRC"
if [[ "$OMX_ROBOT_ID" == omx4 ]]; then
 printf '[F] camera_mode=%s robot_id=%s policy=%s\n' "${OMX4_CAMERA_MODE:-wrist_only}" "${OMX4_LEROBOT_ROBOT_ID:-omx_f_test_follower_arm}" "${OMX4_POLICY_PATH:-<default wrist-only 250k>}"
 if [[ "${OMX4_CAMERA_MODE:-wrist_only}" == top_wrist ]]; then
   echo '[PRECHECK] FUTURE MODEL: top+wrist configured. Check policy image keys before rollout.'
 fi
fi
# Deliberately does not touch motors or connect cameras.
