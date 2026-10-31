#!/usr/bin/env bash
# Read-only external monitor; no camera, motor or MQTT connection.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE="$HOME/.config/logistics_omx/device.env"
if [[ -f "$DEVICE" ]]; then source "$DEVICE"; fi
ID="${3:-${OMX_ROBOT_ID:-}}"
case "$ID" in omx1|omx2|omx3|omx4) ;; *) echo 'Usage: bash scripts/omx_monitor.sh [seconds=YAML] [samples=0] omx1..4' >&2; exit 2;; esac
CONFIG="$ROOT/params/$ID.yaml"
if [[ -f "$ROOT/state/config/$ID.yaml" ]]; then CONFIG="$ROOT/state/config/$ID.yaml"; fi
ARGS=(--samples "${2:-0}")
if [[ -n "${1:-}" ]]; then ARGS+=(--interval "$1"); fi
exec "$ROOT/omx_control" --config "$CONFIG" monitor "${ARGS[@]}"
