#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export OMX_INVENTORY_STATE_PATH="${OMX_INVENTORY_STATE_PATH:-$HOME/.local/state/lerobot/omx_cd_inventory.json}"
exec "${PYTHON_BIN:-python3}" "$SCRIPT_DIR/omx_cd_order_console.py"
