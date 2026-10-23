#!/usr/bin/env bash
# Read-only 5s performance capture, works on omx1-4 without starting motors.
set -Eeuo pipefail
INTERVAL="${1:-5}"
COUNT="${2:-60}"
[[ "$INTERVAL" =~ ^[1-9][0-9]*$ && "$COUNT" =~ ^[1-9][0-9]*$ ]] || {
  echo 'Usage: bash scripts/omx_monitor.sh [interval_sec=5] [samples=60]' >&2; exit 2;
}
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/logs"
LOG="$ROOT/logs/omx_perf_$(hostname)_$(date +%Y%m%d_%H%M%S).log"
echo "[MONITOR] $LOG (Ctrl+C to stop)"
(
 echo "MONITOR host=$(hostname) date=$(date -Is) CPUs=$(nproc) interval=${INTERVAL}s"
 for ((i=1; i<=COUNT; i++)); do
  echo "=== $(date -Is) [$i/$COUNT] ==="
  PID="$(pgrep -f '[l]erobot-rollout' | head -1 || true)"
  if [[ -n "$PID" && -d "/proc/$PID" ]]; then
    ps -p "$PID" -o pid,pcpu,pmem,rss,nlwp,etime,stat,cmd --no-headers || true
    tr '\0' '\n' < "/proc/$PID/environ" 2>/dev/null | grep -E '^(OMP_NUM_THREADS|MKL_NUM_THREADS|OPENBLAS_NUM_THREADS|NUMEXPR_NUM_THREADS)=' || true
  else
    echo 'ROLLOUT=NOT_RUNNING (normal for OMX3/4 only before preload; persistent mode should be running)'
  fi
  vcgencmd measure_temp 2>/dev/null || true
  vcgencmd get_throttled 2>/dev/null || true
  vcgencmd measure_clock arm 2>/dev/null || true
  free -m | head -3
  uptime
  for f in "$ROOT"/state/omx{3,4}_workstation_status.json; do
    [[ -f "$f" ]] && { echo "WORKSTATION_STATUS=$f"; cat "$f"; }
  done
  sleep "$INTERVAL"
 done
) | tee "$LOG"
