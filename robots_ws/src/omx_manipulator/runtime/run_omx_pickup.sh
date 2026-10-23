#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "${1:-}" == "--robot-id" && $# -ge 4 ]] || { echo '사용법: run_omx_pickup.sh --robot-id omx1|omx2 --broker-ip IP' >&2; exit 2; }
case "$2" in omx1|omx2) ;; *) echo 'OMX1/2 피킹 전용' >&2; exit 2 ;; esac
exec "$SCRIPT_DIR/../../../../lerobot/run_omx_backend.sh" "$@"
