#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
else
  source "$HOME/venv/.venv/bin/activate"
fi
export OMX_INVENTORY_STATE_PATH="${OMX_INVENTORY_STATE_PATH:-$HOME/.local/state/lerobot/omx_cd_inventory.json}"

exec lerobot-rollout \
  --strategy.type=base \
  --strategy.order_models_file=omx_cd_order_models.json \
  --strategy.auto_restart_on_home=true \
  --strategy.inventory_capacity=8 \
  --strategy.inventory_state_path="$OMX_INVENTORY_STATE_PATH" \
  --strategy.inventory_reset_on_start=false \
  --strategy.keep_alive_when_inventory_empty=true \
  --strategy.stuck_basket_mask_enabled=true \
  --strategy.stuck_basket_mask_timeout_s=5 \
  --strategy.stuck_basket_mask_duration_s=5 \
  --strategy.stuck_basket_mask_retry_cooldown_s=10 \
  --strategy.stuck_basket_mask_max_retries=2 \
  --strategy.stuck_basket_mask_camera=front \
  --strategy.stuck_basket_mask_polygon='280,4;353,4;360,74;273,74' \
  --strategy.stuck_basket_mask_value=255 \
  --strategy.basket_success_enabled=true \
  --strategy.basket_success_pixel_threshold=25 \
  --strategy.basket_success_changed_ratio=0.02 \
  --strategy.basket_success_min_blob_area=120 \
  --strategy.basket_success_settle_s=0.5 \
  --strategy.home_tolerance=8 \
  --strategy.home_leave_tolerance=12 \
  --strategy.home_hold_s=0.3 \
  --strategy.min_cycle_s=5 \
  --robot.type=omx_follower \
  --robot.port=/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_1B38CA3B503059384C2E3120FF082F2C-if00 \
  --robot.id=omx_cd_follower_arm \
  --robot.cameras="{front: {type: intelrealsense, serial_number_or_name: '920312071264', width: 640, height: 480, fps: 30, use_rgb: true, use_depth: false, warmup_s: 5}, wrist: {type: opencv, index_or_path: '/dev/v4l/by-id/usb-Innomaker_Innomaker-U20CAM-1080p-S1_SN0001-video-index0', width: 640, height: 480, fps: 30, processing_fps: 30}}" \
  --policy.path=outputs/train/warehouse_item_c_160_merged_basket_fix_v1_act_200k/checkpoints/170000/pretrained_model \
  --task="Pick up item C, place it in the basket, and return to the initial pose" \
  --fps=30 \
  --duration=0 \
  --interpolation_multiplier=1 \
  --display_data=false
