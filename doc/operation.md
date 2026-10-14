# 주문 실행 및 현재 제한

OMX01은 A/B, OMX02는 C/D 주문을 처리합니다. 두 라즈베리에서 각각 아래 프로그램을 실행합니다.

## OMX01: A/B 주문

활성 가상환경에서 설치한 LeRobot 폴더로 이동합니다.

터미널 1:
```bash
source ~/venv/.venv/bin/activate
cd ~/il_ws/src/lerobot_omx_release
bash run_ab_orders.sh
```

터미널 2:
```bash
cd ~/il_ws/src/lerobot_omx_release
./omx_order_console.sh
```

`A 2 B 3`을 입력하면 A 2회 완료 후 B 모델로 전환하여 3회 작업합니다.
한 품목은 `A 1`처럼 입력합니다. 같은 품목은 모델 재로딩 없이 다음 주문을 처리합니다.
두 모델의 정규화 설정도 함께 교체합니다. 카메라와 로봇 연결은 유지됩니다.
모델 로딩 실패/알 수 없는 품목이면 동작을 시작하지 않습니다.

## OMX02: C/D 주문

터미널 1:
```bash
source ~/venv/.venv/bin/activate
cd ~/il_ws/src/lerobot_omx_release
./omx_cd_new_order.sh WAIT 0
./run_cd_orders.sh
```

터미널 2:
```bash
cd ~/il_ws/src/lerobot_omx_release
./omx_cd_order_console.sh
```

`C 2 D 3`을 입력하면 C 2회 완료 후 D 170,000스텝 모델로 전환하여 3회 작업합니다.
C/D 주문 상태는 `~/.local/state/lerobot/omx_cd_inventory.json`에 저장되어 A/B 상태와 분리됩니다.

- 시작 시 기존 주문 상태를 대기로 초기화합니다. 재시작 후 주문을 다시 입력하세요.
- 다른 터미널에서 작업 중 새 주문을 덮어쓰는 것은 거절합니다.
- 복합 주문의 아직 전달되지 않은 목록은 콘솔 메모리에 있습니다. 콘솔이 종료되면 자동 복원되지 않습니다.
- 콘솔에서 Ctrl+C는 감시와 뒤의 주문 전달만 중단합니다. 이미 등록된 로봇 주문은 취소하지 않습니다.
- 종료는 추론 터미널에서 수행하세요. 종료 시 초기 위치 복귀 동작이 실행될 수 있습니다.
- 완료 카운트는 초기 자세 복귀 조건과 바구니 영역의 픽셀 변화 판정을 함께 사용합니다. 조명 변화나 가림으로 오판할 수 있으므로 실제 운영 환경에서 임계값을 확인하세요.
- `remaining`은 현재 주문의 남은 작업 수입니다. A/B/C/D 물리 재고를 각각 영구 추적하는 기능은 아직 없습니다.
  `capacity=8` 역시 현재 코드의 주문 카운터 한도이며 자동 재고 관리로 해석하면 안 됩니다.
- `A 2 B 3` 또는 `C 2 D 3`의 품목별 진행시간과 전체 소요시간을 표시합니다. 각 OMX 안에서는 순차 실행입니다.
- Zenoh/관제센터 주문 연동은 아직 포함되지 않았습니다.

전면 RGB 640×480/30FPS + 손목 RGB 640×480/30FPS, depth=false,
제어 목표 30FPS, interpolation_multiplier=1, display_data=false를 사용합니다.

## 카메라 끊김과 정체 대응

- OMX01은 카메라 읽기 실패 시 자동 재연결을 최대 10회 시도합니다.
- OMX01 손목 카메라는 USB 전송량을 줄이기 위해 MJPG를 사용합니다.
- ACT 동작이 일정 시간 시작되지 않으면 전면 영상의 바구니 중앙을 잠시 흰색으로 마스킹해 재추론합니다.
- C/D는 같은 방식의 마스킹 상태와 성공·실패 판정을 주문 콘솔에 표시합니다.
- 마스킹 좌표는 각 OMX의 전면 카메라 구도에 맞춘 값이므로 카메라 위치가 바뀌면 다시 조정해야 합니다.

## MQTT 통합

MQTT 담당자는 `run_omx_mqtt.sh` 안에 별도의 `lerobot-rollout` 옵션을 복사하지 않고 다음 실행 파일을 호출합니다.

| 장치 | ACT 실행 | 주문 전달 |
|---|---|---|
| OMX01 | `./run_ab_orders.sh` | `./omx_new_order.sh ITEM COUNT` |
| OMX02 | `./run_cd_orders.sh` | `./omx_cd_new_order.sh ITEM COUNT` |

세부 절차와 검증 항목은 `OMX_MQTT_실행_스크립트_인수인계_가이드.docx`를 참고하세요.
