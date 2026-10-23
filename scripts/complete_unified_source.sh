#!/usr/bin/env bash
# Complete historical v1 source export from existing, unmodified LeRobot install.
# NOTE: Prefer GitHub main checkout, which already contains these files.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$HOME/il_ws/src/lerobot/src/lerobot"
DEST="$ROOT/lerobot_unified/src/lerobot"
if [[ -f "$DEST/datasets/lerobot_dataset.py" ]]; then
  echo '[OK] LeRobot dataset sources already present'; exit 0
fi
[[ -f "$SOURCE/datasets/lerobot_dataset.py" ]] || { echo '[ERROR] legacy datasets not found; use official GitHub main checkout' >&2; exit 1; }
[[ -d "$DEST" ]] || exit 2
command -v rsync >/dev/null || { echo '[ERROR] rsync required' >&2; exit 1; }
echo '[INFO] Copying ONLY missing source files from the existing LeRobot tree (no overwrite).'
rsync -av --ignore-existing --exclude='__pycache__/' --exclude='*.pyc' "$SOURCE/" "$DEST/"
[[ -f "$DEST/datasets/lerobot_dataset.py" ]] || exit 1
