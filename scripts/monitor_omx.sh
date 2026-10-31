#!/usr/bin/env bash
# Compatibility entry point; all files stay inside the OMX checkout.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/scripts/omx_monitor.sh" "$@"
