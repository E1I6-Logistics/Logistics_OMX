#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "${1:-}" == "--robot-id" && $# -ge 4 ]] || { echo '사용법: run_omx_unload.sh --robot-id omx3|omx4 --broker-ip IP' >&2; exit 2; }
case "$2" in omx3|omx4) ;; *) echo 'OMX3/4 하차 전용' >&2; exit 2 ;; esac
ID="$2"
MODEL_BASE="${OMX_LEROBOT_HOME:-$HOME/Logistics_OMX/lerobot_unified}"
if [[ "$ID" == omx3 ]]; then
  MODEL="${OMX3_POLICY_PATH:-outputs/train/warehouse_item_e_132_merged_act_300k/checkpoints/250000/pretrained_model}"
else
  MODEL="${OMX4_POLICY_PATH:-outputs/train/warehouse_item_f_129_merged_wrist_only_act_300k/checkpoints/250000/pretrained_model}"
  [[ -n "$MODEL" ]] || { echo '[ERROR] OMX4 model must be configured' >&2; exit 1; }
fi
[[ "$MODEL" = /* ]] || MODEL="$MODEL_BASE/$MODEL"
[[ -f "$MODEL/config.json" ]] || { echo "[ERROR] 모델 config.json 없음: $MODEL" >&2; exit 1; }
exec "$SCRIPT_DIR/../../../../lerobot_unified/run_omx_backend.sh" "$@"
