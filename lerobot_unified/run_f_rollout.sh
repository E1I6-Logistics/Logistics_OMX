#!/usr/bin/env bash
set -Eeuo pipefail
echo '[ERROR] Legacy fixed-option launcher disabled on this branch. Use omx_control --config params/omxN.yaml run --confirm-motion.' >&2
exit 2
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
OMX_PYTHON_ENV_PATH="${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}"
[[ -f "$OMX_PYTHON_ENV_PATH/bin/activate" ]] || { echo '[ERROR] Existing venv missing' >&2; exit 1; }
source "$OMX_PYTHON_ENV_PATH/bin/activate"
export PYTHONPATH="$PWD/..:$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
F_POLICY_PATH="${F_POLICY_PATH:-${OMX4_POLICY_PATH:-outputs/train/warehouse_item_f_129_merged_wrist_only_act_300k/checkpoints/250000/pretrained_model}}"
F_FOLLOWER_PORT="${F_FOLLOWER_PORT:-${OMX4_FOLLOWER_PORT:-/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_7E7FE4BC503059384C2E3120FF080E2D-if00}}"
F_ROBOT_ID="${OMX4_LEROBOT_ROBOT_ID:-omx_f_test_follower_arm}"
F_WRIST_CAMERA="${OMX4_WRIST_CAMERA:-/dev/v4l/by-id/usb-Innomaker_Innomaker-U20CAM-1080p-S1_SN0001-video-index0}"
F_MODE="${OMX4_CAMERA_MODE:-wrist_only}"
[[ -e "$F_FOLLOWER_PORT" ]] || { echo "[ERROR] OMX4 OpenRB not found: $F_FOLLOWER_PORT" >&2; exit 1; }
[[ -e "$F_WRIST_CAMERA" ]] || { echo "[ERROR] OMX4 wrist camera not found: $F_WRIST_CAMERA" >&2; exit 1; }
[[ -f "$F_POLICY_PATH/config.json" && -f "$F_POLICY_PATH/model.safetensors" ]] || { echo "[ERROR] OMX4 checkpoint missing: $F_POLICY_PATH" >&2; exit 1; }
CALIBRATION="$HOME/.cache/huggingface/lerobot/calibration/robots/omx_follower/$F_ROBOT_ID.json"
[[ -f "$CALIBRATION" ]] || { echo "[ERROR] OMX4 calibration missing: $CALIBRATION" >&2; exit 1; }
WRIST_CFG="wrist: {type: opencv, index_or_path: '$F_WRIST_CAMERA', width: 640, height: 480, fps: 30, processing_fps: 30, fourcc: 'MJPG', warmup_s: 5}"
case "$F_MODE" in
  wrist_only) CAMERAS="{$WRIST_CFG}" ;;
  top_wrist)
    # Future model only: explicitly select its top camera and policy path.
    case "${OMX4_TOP_CAMERA_TYPE:-opencv}" in
      opencv)
        : "${OMX4_TOP_CAMERA:?Set OMX4_TOP_CAMERA=/dev/v4l/by-id/...}"
        [[ -e "$OMX4_TOP_CAMERA" ]] || { echo "[ERROR] top camera missing: $OMX4_TOP_CAMERA" >&2; exit 1; }
        TOP_CFG="top: {type: opencv, index_or_path: '$OMX4_TOP_CAMERA', width: 640, height: 480, fps: 30, processing_fps: 30, warmup_s: 5}" ;;
      intelrealsense)
        : "${OMX4_TOP_CAMERA_SERIAL:?Set OMX4_TOP_CAMERA_SERIAL}"
        TOP_CFG="top: {type: intelrealsense, serial_number_or_name: '$OMX4_TOP_CAMERA_SERIAL', width: 640, height: 480, fps: 30, use_rgb: true, use_depth: false, warmup_s: 5}" ;;
      *) echo '[ERROR] OMX4_TOP_CAMERA_TYPE must be opencv or intelrealsense' >&2; exit 2 ;;
    esac
    CAMERAS="{$TOP_CFG, $WRIST_CFG}" ;;
  *) echo '[ERROR] OMX4_CAMERA_MODE must be wrist_only or top_wrist' >&2; exit 2 ;;
esac
printf '[F] mode=%s policy=%s robot_id=%s\n' "$F_MODE" "$F_POLICY_PATH" "$F_ROBOT_ID"
exec lerobot-rollout \
  --strategy.type=base \
  --strategy.auto_restart_on_home=false \
  --strategy.workstation_wait_enabled="${OMX_WORKSTATION_MODE:-false}" \
  --strategy.workstation_control_file="${OMX_WORKSTATION_CONTROL_FILE:-}" \
  --strategy.workstation_status_file="${OMX_WORKSTATION_STATUS_FILE:-}" \
  --strategy.idle_complete_enabled="${E_IDLE_COMPLETE_ENABLED:-false}" \
  --strategy.idle_complete_hold_s="${E_IDLE_COMPLETE_HOLD_S:-15}" \
  --strategy.home_tolerance=7 \
  --strategy.home_leave_tolerance=12 \
  --strategy.workstation_job_timeout_s="${OMX_WORKSTATION_TIMEOUT_S:-180}" \
  --robot.type=omx_follower \
  --robot.port="$F_FOLLOWER_PORT" \
  --robot.id="$F_ROBOT_ID" \
  --robot.cameras="$CAMERAS" \
  --policy.path="$F_POLICY_PATH" \
  --task="Pick up item F, place it on the conveyor, and return to the initial pose" \
  --fps=30 \
  --duration=0 \
  --interpolation_multiplier=1 \
  --display_data=false
