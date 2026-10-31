#!/usr/bin/env bash
# Common CPU/runtime policy for OMX1-4. Source from run_omx_mqtt.sh.
# No GPU, accelerator, CPU affinity or real-time scheduler is forced.
# Pi dedicated to OMX; default to physical online CPU count up to 4.
# Benchmark determines optimal policy-specific count; override in profile.env.
ONLINE_CPUS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 2)"
[[ "$ONLINE_CPUS" =~ ^[1-9][0-9]*$ ]] || ONLINE_CPUS=2
[[ "$ONLINE_CPUS" -le 4 ]] || ONLINE_CPUS=4
OMX_CPU_THREADS="${OMX_CPU_THREADS:-$ONLINE_CPUS}"
if [[ ! "${OMX_CPU_THREADS:-2}" =~ ^[1-9][0-9]*$ ]]; then
  echo '[ERROR] OMX_CPU_THREADS must be a positive integer' >&2
  return 1
fi
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-$OMX_CPU_THREADS}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-$OMX_CPU_THREADS}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-$OMX_CPU_THREADS}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-$OMX_CPU_THREADS}"
# OMX_CPU_AFFINITY is opt-in, e.g. 1-3. Never assume CPUs 0..3 exist.
OMX_RUN_PREFIX=()
if [[ -n "${OMX_CPU_AFFINITY:-}" ]]; then
  if ! command -v taskset >/dev/null 2>&1; then
    echo '[ERROR] OMX_CPU_AFFINITY requires taskset' >&2
    return 1
  fi
  if ! taskset -c "$OMX_CPU_AFFINITY" true 2>/dev/null; then
    echo "[ERROR] Invalid/unavailable OMX_CPU_AFFINITY=$OMX_CPU_AFFINITY" >&2
    return 1
  fi
  OMX_RUN_PREFIX=(taskset -c "$OMX_CPU_AFFINITY")
fi
printf '[RUNTIME] CPU threads: OMP=%s MKL=%s OPENBLAS=%s NUMEXPR=%s\n' "$OMP_NUM_THREADS" "$MKL_NUM_THREADS" "$OPENBLAS_NUM_THREADS" "$NUMEXPR_NUM_THREADS"
printf '[RUNTIME] CPU affinity: %s\n' "${OMX_CPU_AFFINITY:-OS default (not pinned)}"
