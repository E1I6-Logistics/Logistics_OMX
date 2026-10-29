#!/usr/bin/env bash
# YAML is the only source of runtime/model/home settings.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEVICE="$HOME/.config/logistics_omx/device.env"
if [[ -f "$DEVICE" ]]; then source "$DEVICE"; fi
ID="${OMX_ROBOT_ID:-}"
CONFIG=""
CONFIRM=()
while [[ $# -gt 0 ]]; do
 case "$1" in
 --robot-id) ID="${2:?}"; shift 2;;
 --config) CONFIG="${2:?}"; shift 2;;
 --confirm-motion) CONFIRM=(--confirm-motion); shift;;
 *) echo "Usage: $0 --robot-id omx1 [--config params/omx1.yaml] --confirm-motion" >&2; exit 2;;
 esac
done
if [[ -z "$CONFIG" ]]; then
 case "$ID" in omx1|omx2|omx3|omx4) CONFIG="$ROOT/params/$ID.yaml";; *) echo '[ERROR] --robot-id omx1..4 required' >&2; exit 2;; esac
fi
export OMX_PYTHON_ENV_PATH="${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}"
exec "$ROOT/omx_control" --config "$CONFIG" run "${CONFIRM[@]}"
