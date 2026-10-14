#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
else
  source "$HOME/venv/.venv/bin/activate"
fi

exec lerobot-rollout \
  --strategy.type=base \
  --strategy.order_models_file=omx_order_models.json \
  --strategy.auto_restart_on_home=true \
  --strategy.inventory_capacity=8 \
  --strategy.inventory_reset_on_start=false \
  --strategy.keep_alive_when_inventory_empty=true \
  --strategy.camera_auto_reconnect=true \
  --strategy.camera_reconnect_attempts=10 \
  --strategy.camera_reconnect_interval_s=1 \
  --strategy.stuck_basket_mask_enabled=true \
  --strategy.stuck_basket_mask_timeout_s=5 \
  --strategy.stuck_basket_mask_duration_s=2 \
  --strategy.stuck_basket_mask_retry_cooldown_s=10 \
  --strategy.stuck_basket_mask_max_retries=2 \
  --strategy.stuck_basket_mask_camera=front \
  --strategy.stuck_basket_mask_polygon='278,10;350,10;358,75;268,75' \
  --strategy.stuck_basket_mask_value=255 \
  --strategy.home_tolerance=8 \
  --strategy.home_leave_tolerance=12 \
  --strategy.home_hold_s=0.3 \
  --strategy.min_cycle_s=5 \
  --robot.type=omx_follower \
  --robot.port=/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_4256A5C6503059384C2E3120FF08242F-if00 \
  --robot.id=omx_follower_arm \
  --robot.cameras="{front: {type: intelrealsense, serial_number_or_name: '920312071050', width: 640, height: 480, fps: 30, use_rgb: true, use_depth: false, warmup_s: 5}, wrist: {type: opencv, index_or_path: '/dev/v4l/by-id/usb-Innomaker_Innomaker-U20CAM-1080p-S1_SN0001-video-index0', width: 640, height: 480, fps: 30, processing_fps: 30, fourcc: 'MJPG'}}" \
  --policy.path=outputs/train/warehouse_all_200_two_cycles_v3_act_250k/checkpoints/150000/pretrained_model \
  --task="Pick up the topmost item, place it in the basket, and return to the initial pose" \
  --fps=30 \
  --duration=0 \
  --interpolation_multiplier=1 \
  --display_data=false
