#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$ROOT" == "$HOME/Logistics_OMX" ]] || { echo "[ERROR] 저장소는 ~/Logistics_OMX에 배치하세요: $ROOT" >&2; exit 2; }
ID=""; BROKER="192.168.0.16"; SKIP_DEPS=0; SYNC_REQS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --robot-id) [[ $# -ge 2 ]] || exit 2; ID="$2"; shift 2 ;;
    --broker-ip) [[ $# -ge 2 ]] || exit 2; BROKER="$2"; shift 2 ;;
    --skip-deps) SKIP_DEPS=1; shift ;;
    --sync-requirements) SYNC_REQS=1; shift ;;
    *) echo "[ERROR] 알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done
if (( SKIP_DEPS && SYNC_REQS )); then
  echo '[ERROR] --skip-deps와 --sync-requirements를 동시에 사용할 수 없습니다.' >&2
  exit 2
fi
case "$ID" in omx1|omx2|omx3|omx4) ;; *) echo '[ERROR] --robot-id omx1~4 필수' >&2; exit 2;; esac
SRC="$ROOT/robots_ws/src/omx_manipulator"
TARGET="$ROOT/lerobot"
SETTINGS="$HOME/.config/logistics_omx/device.env"
MARKER="$HOME/.config/logistics_omx/source_installed"
if [[ ! -d "$TARGET/.git" ]]; then
  [[ ! -e "$TARGET" ]] || { echo "[ERROR] 기존 LeRobot 디렉터리 보존: $TARGET" >&2; exit 1; }
  git clone https://github.com/huggingface/lerobot.git "$TARGET"
  git -C "$TARGET" checkout --detach 89236ea0f4f81a81ca566081e20dd1ff5f823cbe
fi
if [[ ! -f "$MARKER" ]]; then
  # Don't clobber previously customized LeRobot that isn't managed by this installer.
  if ! git -C "$TARGET" diff --quiet; then
    echo '[ERROR] 기존 LeRobot 수정 사항이 발견되어 자동 덮어쓰기를 중단합니다.' >&2
    exit 1
  fi
  cp -a "$SRC/overlay/." "$TARGET/"
  if [[ "$ID" == omx3 || "$ID" == omx4 ]]; then
    cp -a "$SRC/overlay_workstation/." "$TARGET/"
  fi
  mkdir -p "$(dirname "$MARKER")"
  printf '%s\n' "$ID" > "$MARKER"
else
  OLD_ID="$(cat "$MARKER")"
  [[ "$OLD_ID" == "$ID" ]] || { echo "[ERROR] 기존 설치는 $OLD_ID, 요청은 $ID: 자동 변경하지 않습니다" >&2; exit 1; }
  echo '[INFO] LeRobot Overlay 설치 이력 존재: 덮어쓰기 생략'
fi
# Role-specific source code; state files, model weights, calibration remain untouched.
for f in omx_mqtt_client.py run_e_rollout.sh run_omx_backend.sh omx_runtime_env.sh omx_status.sh; do
  cp -a "$SRC/runtime/$f" "$TARGET/$f"
done
for f in omx_order_models.json omx_cd_order_models.json omx_new_order.sh; do
  if [[ ! -f "$TARGET/$f" ]]; then
    cp -a "$SRC/runtime/$f" "$TARGET/$f"
  fi
done
chmod +x "$ROOT/run_omx_mqtt.sh" "$SRC/runtime/run_omx_pickup.sh" "$SRC/runtime/run_omx_unload.sh" \
   "$TARGET/run_omx_backend.sh" "$TARGET/run_e_rollout.sh"
if [[ ! -f "$ROOT/.venv/bin/activate" ]]; then python3 -m venv "$ROOT/.venv"; fi
if (( ! SKIP_DEPS )); then
  if [[ "$ID" == omx1 || "$ID" == omx2 || "$ID" == omx4 ]]; then
    EXTRA='[dynamixel,intelrealsense,dataset]'
  else
    EXTRA='[dynamixel,dataset]'
  fi
  "$ROOT/.venv/bin/python" -m pip install -e "$TARGET$EXTRA" paho-mqtt
  # Opt-in: version sync may reinstall large torch/torchvision packages.
  if (( SYNC_REQS )); then
    if [[ "$ID" == omx1 || "$ID" == omx2 || "$ID" == omx4 ]]; then
      REQ_FILE="$ROOT/requirements-realsense.txt"
    else
      REQ_FILE="$ROOT/requirements.txt"
    fi
    echo "[REQUIREMENTS] 명시적으로 요청한 버전 동기화: $REQ_FILE"
    "$ROOT/.venv/bin/python" -m pip install -r "$REQ_FILE"
  fi
  "$ROOT/.venv/bin/python" -m pip check
fi
mkdir -p "$(dirname "$SETTINGS")" "$ROOT/state"
if [[ ! -f "$SETTINGS" ]]; then
  printf 'OMX_ROBOT_ID=%q\nOMX_BROKER_IP=%q\n' "$ID" "$BROKER" > "$SETTINGS"
else
  if ! grep -Eq "^OMX_ROBOT_ID=$ID$" "$SETTINGS"; then
    echo "[WARN] 기존 장치 설정이 있습니다. $SETTINGS 내용을 직접 확인하세요." >&2
  fi
fi
PROFILE="$HOME/.config/logistics_omx/profile.env"
if [[ ! -f "$PROFILE" ]]; then
  cp -a "$SRC/runtime/profiles/$ID.env.example" "$PROFILE"
  echo "[PROFILE] $PROFILE 생성. USB/카메라 등 현장별 설정을 수정할 수 있습니다."
else
  echo '[PROFILE] 기존 설정 유지, 중복 생성하지 않음'
fi
BASHRC="$HOME/.bashrc"
LINE='source "$HOME/Logistics_OMX/scripts/omx_shell.sh" # Logistics_OMX shell integration'
if ! grep -Fq 'Logistics_OMX shell integration' "$BASHRC" 2>/dev/null; then
  printf '\n%s\n' "$LINE" >> "$BASHRC"
  echo '[SHELL] ~/.bashrc 등록 완료'
else
  echo '[SHELL] ~/.bashrc에 이미 등록됨, 중복 등록하지 않음'
fi
bash -n "$ROOT/run_omx_mqtt.sh" "$SRC/runtime/run_omx_pickup.sh" "$SRC/runtime/run_omx_unload.sh" "$TARGET/run_omx_backend.sh"
"$ROOT/.venv/bin/python" -m py_compile "$TARGET/omx_mqtt_client.py" "$TARGET/src/lerobot/rollout/configs.py" "$TARGET/src/lerobot/rollout/strategies/base.py"
echo "[OK] $ID 설치 완료. source ~/.bashrc 실행 후 omx_start"
echo '[INFO] 모델 데이터와 실제 모터/카메라 경로는 별도 검증이 필요합니다.'
