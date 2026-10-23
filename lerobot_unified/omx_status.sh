#!/usr/bin/env bash
set -Eeuo pipefail
printf '=== System ===\n'; uname -m; nproc; uptime
printf '\n=== Runtime override ===\n'
printf 'OMX_CPU_THREADS=%s OMX_CPU_AFFINITY=%s\n' "${OMX_CPU_THREADS:-2}" "${OMX_CPU_AFFINITY:-none}"
printf '\n=== OpenRB USB ports ===\n'
find /dev/serial/by-id -maxdepth 1 -name 'usb-ROBOTIS_OpenRB-150_*' -print 2>/dev/null || true
printf '\n=== Video devices ===\n'
find /dev/v4l/by-id -maxdepth 1 -name '*-video-index0' -print 2>/dev/null || true
printf '\n=== Relevant processes (snapshot) ===\n'
ps -eo pid,ppid,stat,pcpu,pmem,args | grep -E '[l]erobot-rollout|[o]mx_mqtt_client.py|[r]un_e_rollout.sh' || true
printf '\nNote: process/device visibility does not prove motion or MQTT connectivity.\n'
