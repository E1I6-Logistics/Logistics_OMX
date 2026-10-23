#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$ROOT" == "$HOME/Logistics_OMX" ]] || { echo '[ERROR] ~/Logistics_OMX 에서 실행하세요' >&2; exit 2; }
DEVICE="$HOME/.config/logistics_omx/device.env"
[[ -f "$DEVICE" ]] || { echo '[ERROR] 설치를 먼저 수행하세요: scripts/install_omx.sh' >&2; exit 1; }
source "$DEVICE"
case "$OMX_ROBOT_ID" in omx1|omx2|omx3|omx4) ;; *) exit 2;; esac
if pgrep -af '[l]erobot-rollout|[o]mx_mqtt_client.py' >/dev/null 2>&1; then
  echo '[ERROR] Rollout/MQTT 실행 중. 안전하게 종료하고 업데이트하세요.' >&2
  exit 1
fi
SRC="$ROOT/robots_ws/src/omx_manipulator"
TARGET="$ROOT/lerobot"
[[ -d "$TARGET/src/lerobot" ]] || { echo '[ERROR] LeRobot 미설치' >&2; exit 1; }
BACKUP="$ROOT/state/code_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP"
FILES=(
  src/lerobot/cameras/opencv/camera_opencv.py
  src/lerobot/cameras/opencv/configuration_opencv.py
  src/lerobot/policies/act/modeling_act.py
  src/lerobot/processor/normalize_processor.py
  src/lerobot/rollout/configs.py
  src/lerobot/rollout/order_models.py
  src/lerobot/rollout/strategies/base.py
  src/lerobot/scripts/lerobot_record.py
  omx_mqtt_client.py
  run_e_rollout.sh
  run_omx_backend.sh
  omx_runtime_env.sh
  omx_status.sh
)
for file in "${FILES[@]}"; do
  dest="$TARGET/$file"
  if [[ -f "$dest" ]]; then
    mkdir -p "$BACKUP/$(dirname "$file")"
    cp -a "$dest" "$BACKUP/$file"
  fi
done
# Apply original overlay first, then role-specific workstation changes.
cp -a "$SRC/overlay/." "$TARGET/"
if [[ "$OMX_ROBOT_ID" == omx3 || "$OMX_ROBOT_ID" == omx4 ]]; then
  cp -a "$SRC/overlay_workstation/." "$TARGET/"
fi
for file in omx_mqtt_client.py run_e_rollout.sh run_omx_backend.sh omx_runtime_env.sh omx_status.sh; do
  cp -a "$SRC/runtime/$file" "$TARGET/$file"
done
bash -n "$TARGET/run_omx_backend.sh" "$TARGET/run_e_rollout.sh"
"$ROOT/.venv/bin/python" -m py_compile "$TARGET/omx_mqtt_client.py" "$TARGET/src/lerobot/rollout/configs.py" "$TARGET/src/lerobot/rollout/strategies/base.py"
echo "[OK] 업데이트 적용: $OMX_ROBOT_ID"
echo "[BACKUP] 기존 코드: $BACKUP"
echo '모델, 캘리브레이션, 로컬 장치 설정은 변경하지 않았습니다.'
