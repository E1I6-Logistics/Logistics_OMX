#!/usr/bin/env bash
set -Eeuo pipefail

ITEM="${1:-}"
QUANTITY="${2:-}"
CAPACITY="${3:-8}"
STATE_PATH="${OMX_INVENTORY_STATE_PATH:-$HOME/.local/state/lerobot/omx_inventory.json}"

if [[ -z "$ITEM" ]] || [[ ! "$QUANTITY" =~ ^[0-9]+$ ]] || [[ ! "$CAPACITY" =~ ^[0-9]+$ ]] || (( CAPACITY < 1 || QUANTITY > CAPACITY )); then
  echo "사용법: $0 물건이름 주문수량 [전체재고수량]  (예: $0 A 2, 대기: $0 WAIT 0)" >&2
  exit 1
fi

# 상태 확인은 읽기만 수행하며, 장치가 없어도 기존 주문 등록을 막지 않는다.
show_connection_status() {
  echo "========== Raspberry Pi 장치/프로세스 상태 =========="

  local -a motor_ports=() camera_ports=()
  local port
  shopt -s nullglob
  motor_ports=(/dev/serial/by-id/usb-ROBOTIS_OpenRB-150_*)
  camera_ports=(/dev/v4l/by-id/*-video-index0)
  shopt -u nullglob

  if (( ${#motor_ports[@]} > 0 )); then
    for port in "${motor_ports[@]}"; do
      if [[ -e "$port" && -r "$port" && -w "$port" ]]; then
        echo "[모터 컨트롤러] 장치 있음, 읽기/쓰기 권한 있음: $port"
      elif [[ -e "$port" ]]; then
        echo "[모터 컨트롤러] 장치 있음, 권한 확인 필요: $port"
      else
        echo "[모터 컨트롤러] 장치 링크 끊김: $port"
      fi
    done
  else
    echo "[모터 컨트롤러] OpenRB-150 장치 미발견"
  fi

  if (( ${#camera_ports[@]} > 0 )); then
    for port in "${camera_ports[@]}"; do
      if [[ -e "$port" && -r "$port" ]]; then
        echo "[카메라] 장치 있음, 읽기 권한 있음: $port"
      elif [[ -e "$port" ]]; then
        echo "[카메라] 장치 있음, 권한 확인 필요: $port"
      else
        echo "[카메라] 장치 링크 끊김: $port"
      fi
    done
  else
    echo "[카메라] /dev/v4l/by-id 카메라 미발견"
  fi

  if pgrep -af '[l]erobot-rollout|[r]un_e_interactive.py' >/dev/null 2>&1; then
    echo "[LeRobot] 관련 실행 프로세스 발견:"
    pgrep -af '[l]erobot-rollout|[r]un_e_interactive.py' || true
  else
    echo "[LeRobot] 관련 실행 프로세스 없음"
  fi

  if pgrep -af '[o]mx_mqtt_client.py' >/dev/null 2>&1; then
    echo "[MQTT 클라이언트] 관련 실행 프로세스 발견:"
    pgrep -af '[o]mx_mqtt_client.py' || true
  else
    echo "[MQTT 클라이언트] 관련 실행 프로세스 없음"
  fi

  echo "※ 장치 존재/프로세스 확인일 뿐, 실제 모터 통신·영상 수신·브로커 연결 성공을 보장하지 않습니다."
  echo "====================================================="
}

show_connection_status

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" - "$STATE_PATH" "$CAPACITY" "$QUANTITY" "$ITEM" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
capacity = int(sys.argv[2])
quantity = int(sys.argv[3])
item = sys.argv[4].upper()
if item not in {"A", "B", "WAIT"} or (item == "WAIT") != (quantity == 0):
    raise SystemExit("주문은 A 1~8 또는 B 1~8, 대기는 WAIT 0으로 입력하세요.")
if quantity > 0 and path.exists():
    previous = json.loads(path.read_text())
    if int(previous.get("remaining", 0)) > 0:
        raise SystemExit("진행 중인 주문이 있습니다. 완료 후 다음 주문을 입력하세요.")
state = {
    "capacity": capacity,
    "total_completed": capacity - quantity,
    "remaining": quantity,
    "order_item": item,
    "order_quantity": quantity,
    "order_completed": 0,
    "order_started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "order_started_at_epoch": time.time(),
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, path)
if quantity == 0:
    print(f"Waiting mode set: state={path}")
else:
    print(f"New order accepted: item={item}, quantity={quantity}, state={path}")
PY
