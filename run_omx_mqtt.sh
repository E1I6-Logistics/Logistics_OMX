#!/usr/bin/env bash
# YAML is the only source of runtime/model/home settings.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEVICE="$HOME/.config/logistics_omx/device.env"
if [[ -f "$DEVICE" ]]; then source "$DEVICE"; fi
REGISTRY="$HOME/.config/logistics_omx/shell.env"
if [[ -f "$REGISTRY" ]]; then source "$REGISTRY"; fi
export OMX_PYTHON
ID="${OMX_ROBOT_ID:-}"
CONFIG="${OMX_CONFIG_PATH:-}"
CONFIRM=()
DIAGNOSTICS=()
EXPECTED=()
while [[ $# -gt 0 ]]; do
 case "$1" in
 --robot-id) ID="${2:?}"; shift 2;;
 --config) CONFIG="${2:?}"; shift 2;;
 --confirm-motion) CONFIRM=(--confirm-motion); shift;;
 --diagnostics|--important-only) DIAGNOSTICS+=("$1"); shift;;
 *) echo "Usage: $0 --robot-id omx1 [--config params/omx1.yaml] --confirm-motion [--diagnostics] [--important-only]" >&2; exit 2;;
 esac
done
if [[ -z "$CONFIG" ]]; then
 case "$ID" in omx1|omx2|omx3|omx4)
   if [[ -f "$ROOT/state/config/$ID.yaml" ]]; then CONFIG="$ROOT/state/config/$ID.yaml"; else CONFIG="$ROOT/params/$ID.yaml"; fi;; *) echo '[ERROR] --robot-id omx1..4 required' >&2; exit 2;; esac
fi
if [[ -n "$ID" ]]; then EXPECTED=(--expected-device "$ID"); fi
export OMX_PYTHON_ENV_PATH="${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}"
exec "$ROOT/omx_control" --config "$CONFIG" run "${CONFIRM[@]}" "${DIAGNOSTICS[@]}" "${EXPECTED[@]}"
