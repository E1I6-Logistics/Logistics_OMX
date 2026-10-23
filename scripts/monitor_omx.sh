#!/usr/bin/env bash
# Read-only; no motor or MQTT commands. Usage: bash monitor_omx.sh [seconds=5] [samples=24]
set -euo pipefail
interval="${1:-5}"; samples="${2:-24}"
[[ "$interval" =~ ^[1-9][0-9]*$ && "$samples" =~ ^[1-9][0-9]*$ ]] || { echo 'Usage: bash monitor_omx.sh [interval integer seconds] [samples integer]' >&2; exit 2; }
mkdir -p "$HOME/omx_diagnostics"
out="$HOME/omx_diagnostics/$(hostname)_$(date +%Y%m%d_%H%M%S).csv"
echo 'date,pid,elapsed_s,cpu_pct_cumulative,rss_kb,threads,temperature_c,throttled' > "$out"
for ((i=0;i<samples;i++)); do
 temp="unknown"; throttle="unknown"
 if command -v vcgencmd >/dev/null; then temp="$(vcgencmd measure_temp 2>/dev/null | sed -n "s/temp=\([0-9.]*\)'C/\1/p")"; throttle="$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)"; fi
 mapfile -t pids < <(pgrep -f '^.*[/]bin/[l]erobot-rollout|^.*[l]erobot-rollout --strategy' || true)
 if ((${#pids[@]}==0)); then echo "$(date -Is),NONE,,,,,${temp:-unknown},${throttle:-unknown}" | tee -a "$out"; else
  for pid in "${pids[@]}"; do
    line="$(ps -p "$pid" -o etimes=,%cpu=,rss=,nlwp= 2>/dev/null | awk '{$1=$1;print}' || true)"
    if [[ -n "$line" ]]; then read -r elapsed cpu rss threads <<< "$line"; echo "$(date -Is),$pid,$elapsed,$cpu,$rss,$threads,${temp:-unknown},${throttle:-unknown}" | tee -a "$out"; fi
  done
 fi
 if ((i+1<samples)); then sleep "$interval"; fi
done
echo "[SAVED] $out"
