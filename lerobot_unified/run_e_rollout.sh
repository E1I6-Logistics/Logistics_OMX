#!/usr/bin/env bash
set -Eeuo pipefail
echo '[ERROR] Legacy fixed-option launcher disabled on this branch. Use omx_control --config params/omxN.yaml run --confirm-motion.' >&2
exit 2

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
OMX_PYTHON_ENV_PATH="${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}"
[[ -f "$OMX_PYTHON_ENV_PATH/bin/activate" ]] || { echo '[ERROR] 기존 venv 없음' >&2; exit 1; }
source "$OMX_PYTHON_ENV_PATH/bin/activate"
export PYTHONPATH="$PWD/..:$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

: "${E_POLICY_PATH:?E_POLICY_PATH에 E ACT pretrained_model 경로를 지정하세요.}"
: "${E_FOLLOWER_PORT:?E_FOLLOWER_PORT에 E 팔로워 OpenRB by-id 경로를 지정하세요.}"

# OMX3: OpenCV (C920). OMX4: Intel RealSense front RGB.
# Preserve the camera key expected by the policy (default: top).
CAMERA_KEY="${E_PRIMARY_CAMERA_KEY:-top}"
[[ "$CAMERA_KEY" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || {
  echo "[ERROR] invalid E_PRIMARY_CAMERA_KEY: $CAMERA_KEY" >&2; exit 2;
}
WRIST_CAMERA="${E_WRIST_CAMERA:-/dev/v4l/by-id/usb-Innomaker_Innomaker-U20CAM-1080p-S1_SN0001-video-index0}"
[[ -e "$WRIST_CAMERA" ]] || {
  echo "[ERROR] wrist camera missing: $WRIST_CAMERA" >&2; exit 1;
}
case "${E_CAMERA_TYPE:-opencv}" in
  opencv)
    PRIMARY_CAMERA="${E_TOP_CAMERA:-/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_5FFFC43F-video-index0}"
    [[ -e "$PRIMARY_CAMERA" ]] || {
      echo "[ERROR] OpenCV primary camera missing: $PRIMARY_CAMERA" >&2; exit 1;
    }
    PRIMARY_CFG="$CAMERA_KEY: {type: opencv, index_or_path: '$PRIMARY_CAMERA', width: 640, height: 480, fps: 30, processing_fps: 30, warmup_s: 5}"
    ;;
  intelrealsense)
    : "${E_REALSENSE_SERIAL:?OMX4 RealSense 시리얼(E_REALSENSE_SERIAL)을 지정하세요.}"
    PRIMARY_CFG="$CAMERA_KEY: {type: intelrealsense, serial_number_or_name: '$E_REALSENSE_SERIAL', width: 640, height: 480, fps: 30, use_rgb: true, use_depth: false, warmup_s: 5}"
    ;;
  *) echo "[ERROR] unsupported E_CAMERA_TYPE: $E_CAMERA_TYPE" >&2; exit 2 ;;
esac
CAMERA_CFG="{$PRIMARY_CFG, wrist: {type: opencv, index_or_path: '$WRIST_CAMERA', width: 640, height: 480, fps: 30, processing_fps: 30, warmup_s: 5}}"

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
  --robot.port="$E_FOLLOWER_PORT" \
  --robot.id="${E_ROBOT_ID:-omx_e_follower_arm}" \
  --robot.cameras="$CAMERA_CFG" \
  --policy.path="$E_POLICY_PATH" \
  --task="Pick up the topmost accessible item from the basket, place it on the conveyor, and return to the initial pose" \
  --fps=30 \
  --duration=0 \
  --interpolation_multiplier=1 \
  --display_data="${E_DISPLAY_DATA:-true}"
