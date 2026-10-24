#!/usr/bin/env bash
# Sync-only compatibility settings. Unsafe ACT prefetch cannot be enabled.
set -Eeuo pipefail
FILE="${OMX_PREFETCH_ENV_FILE:-$HOME/.config/omx/act_prefetch.env}"
case "${1:-status}" in
  status)
    echo "Settings: $FILE"
    if [[ -f "$FILE" ]]; then cat "$FILE"; else echo 'No settings: default synchronous inference'; fi
    ;;
  on)
    echo "[ERROR] ACT prefetch was removed after unsafe physical behavior. Sync-only runtime." >&2
    exit 2
    ;;
  off)
    mkdir -p -- "$(dirname -- "$FILE")"
    cat >"$FILE" <<'EOF'
# Roll back to unchanged synchronous ACT inference.
export OMX_ACT_PREFETCH=0
export OMX_CPU_THREADS=4
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
export NUMEXPR_NUM_THREADS=4
EOF
    echo "[OK] Sync ACT configuration saved for NEXT launcher start: $FILE"
    ;;
  *) echo 'Usage: bash scripts/set_omx_optimization.sh on|off|status' >&2; exit 2;;
esac
