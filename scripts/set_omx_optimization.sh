#!/usr/bin/env bash
# Local Raspberry Pi settings, no model/checkpoint modification.
set -Eeuo pipefail
FILE="${OMX_PREFETCH_ENV_FILE:-$HOME/.config/omx/act_prefetch.env}"
case "${1:-status}" in
  status)
    echo "Settings: $FILE"
    if [[ -f "$FILE" ]]; then cat "$FILE"; else echo 'No settings: default synchronous inference'; fi
    ;;
  on)
    mkdir -p -- "$(dirname -- "$FILE")"
    cat >"$FILE" <<'EOF'
# Shared by OMX1 / OMX2 / OMX3 / OMX4 on this Raspberry Pi.
# Same checkpoints, cameras, Home logic and MQTT protocol.
export OMX_ACT_PREFETCH=1
export OMX_ACT_PREFETCH_STEPS=70
export OMX_ACT_PREFETCH_MAX_AGE_S=4.0
export OMX_ACT_PREFETCH_STALL_S=10.0
# Reserve one CPU core for control/cameras while ACT runs on worker thread.
export OMX_CPU_THREADS=3
export OMP_NUM_THREADS=3
export MKL_NUM_THREADS=3
export OPENBLAS_NUM_THREADS=3
export NUMEXPR_NUM_THREADS=3
EOF
    echo "[OK] OMX optimization enabled for NEXT launcher start: $FILE"
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
    echo "[OK] OMX optimization disabled for NEXT launcher start: $FILE"
    ;;
  *) echo 'Usage: bash scripts/set_omx_optimization.sh on|off|status' >&2; exit 2;;
esac
