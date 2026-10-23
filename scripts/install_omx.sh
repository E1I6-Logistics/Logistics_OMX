#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$ROOT" == "$HOME/Logistics_OMX" ]] || { echo "[ERROR] 저장소는 ~/Logistics_OMX에 배치하세요: $ROOT" >&2; exit 2; }
ID=""; BROKER="192.168.0.16"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --robot-id) [[ $# -ge 2 ]] || exit 2; ID="$2"; shift 2 ;;
    --broker-ip) [[ $# -ge 2 ]] || exit 2; BROKER="$2"; shift 2 ;;
    --skip-deps) shift ;; # Legacy alias: existing venv is always reused.
    --sync-requirements) echo "[ERROR] Existing environments are preserved; use no --sync-requirements." >&2; exit 2 ;;
    *) echo "[ERROR] 알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done
case "$ID" in omx1|omx2|omx3|omx4) ;; *) echo '[ERROR] --robot-id omx1~4 필수' >&2; exit 2;; esac
# The existing production venv is the source of truth, never recreate or upgrade it.
PYENV="${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}"
if [[ ! -x "$PYENV/bin/python" || ! -f "$PYENV/bin/activate" ]]; then
  echo "[ERROR] 기존 Python venv가 없습니다: $PYENV" >&2
  echo '[INFO] 신규 환경을 자동 설치하지 않습니다. 기존 venv 경로를 지정하세요.' >&2
  exit 1
fi
"$PYENV/bin/python" - "$ID" <<'PYENV_CHECK'
import importlib.metadata as im
import sys
required = ('lerobot', 'torch', 'dynamixel-sdk', 'paho-mqtt')
if sys.argv[1] in ('omx1','omx2','omx4'):
    required += ('pyrealsense2',)
missing = []
for package in required:
    try:
        print(f'[VENV] {package}=={im.version(package)}')
    except im.PackageNotFoundError:
        missing.append(package)
if missing:
    raise SystemExit('[ERROR] 기존 venv에 필요한 패키지 없음: ' + ', '.join(missing))
version = im.version('lerobot')
if version != '0.6.2':
    raise SystemExit('[ERROR] 기존 lerobot 버전 불일치: ' + version + ' (기준 0.6.2)')
PYENV_CHECK
# Snapshot installed versions before modifying any source tree. No packages are changed.
mkdir -p "$ROOT/state"
"$PYENV/bin/python" -m pip freeze > "$ROOT/state/pip_existing_before_setup.txt"
echo "[VENV] 기존 Python 환경 유지: $PYENV"
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
# Deliberately no pip install. Preserve torch, torchvision, pyrealsense2, etc.
# Use this venv's executable and select the new source via PYTHONPATH at runtime.
mkdir -p "$(dirname "$SETTINGS")" "$ROOT/state"
if [[ ! -f "$SETTINGS" ]]; then
  printf 'OMX_ROBOT_ID=%q\nOMX_BROKER_IP=%q\nOMX_PYTHON_ENV_PATH=%q\n' "$ID" "$BROKER" "$PYENV" > "$SETTINGS"
else
  if ! grep -Eq "^OMX_ROBOT_ID=$ID$" "$SETTINGS"; then
    echo "[WARN] 기존 장치 설정이 있습니다. $SETTINGS 내용을 직접 확인하세요." >&2
  fi
fi
if ! grep -q '^OMX_PYTHON_ENV_PATH=' "$SETTINGS"; then
  printf 'OMX_PYTHON_ENV_PATH=%q\n' "$PYENV" >> "$SETTINGS"
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
"$PYENV/bin/python" -m py_compile "$TARGET/omx_mqtt_client.py" "$TARGET/src/lerobot/rollout/configs.py" "$TARGET/src/lerobot/rollout/strategies/base.py"
echo "[OK] $ID 소스 설치 완료. 기존 가상환경 버전 유지. source ~/.bashrc 후 omx_start"
echo '[INFO] 모델 데이터와 실제 모터/카메라 경로는 별도 검증이 필요합니다.'
