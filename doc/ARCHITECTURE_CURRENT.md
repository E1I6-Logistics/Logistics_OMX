# Logistics_OMX 현재 구조 · 정리 1단계

대상: `refactor/omx-unified-runtime` (기준 `fix/omx-safety-stability`의 `e2d9dd3`)

## 목적과 안전 원칙

- `omx_start` 실행 경로와 ACT 동작은 **변경하지 않는다**.
- 기존 ACT 체크포인트, 카메라 키·설정, 모터 제어, MQTT 프로토콜, Home 판정, 동기 추론 지연 모두 유지한다.
- 기존 Prefetch는 **OFF 유지**. 실기에서 비정상 동작이 발생했으므로 `scripts/set_omx_optimization.sh on` **실행 금지**.
- 이전 사본/구형 Overlay 등 명확히 실행 경로 밖에 있는 파일만 1차 제거한다. Git 기록으로 복구할 수 있다.
- 배포는 비구동 정적 검사 이후 OMX1 -> OMX2 -> OMX3 -> OMX4 순서로 진행한다.

## 현재의 실제 호출 흐름

```text
omx_start (scripts/omx_shell.sh)
  -> run_omx_mqtt.sh
      ├─ omx1/omx2: robots_ws/src/omx_manipulator/runtime/run_omx_pickup.sh
      └─ omx3/omx4: robots_ws/src/omx_manipulator/runtime/run_omx_unload.sh
  -> lerobot_unified/run_omx_backend.sh
      ├─ lerobot_unified/omx_mqtt_client.py
      └─ lerobot-rollout (lerobot_unified/src/lerobot)
          ├─ OMX1/2: 주문 JSON, ACT 모델 교체, Home 완료 카운트
          └─ OMX3/4: session별 START/CLEAR JSON, READY/RUNNING/DONE
          -> Sync ACT -> Action 전/후처리 -> OmxFollower -> OpenRB/Dynamixel
```

MQTT는 FMS와 각 Pi5 사이의 명령/상태/결과 전송 수단이며 직접 모터 명령을 보내지 않는다.

## 현행 유지 대상

- `lerobot_unified/` 전체 (모델 정책, 카메라·모터, `run_omx_backend.sh`, `omx_mqtt_client.py`, `run_e_rollout.sh`, `run_f_rollout.sh`)
- `robots_ws/src/omx_manipulator/runtime/run_omx_pickup.sh`, `run_omx_unload.sh`, `profiles/`
- `run_omx_mqtt.sh`와 `scripts/install_omx.sh`, `omx_shell.sh`, `preflight_omx.sh`
- `tests/`, 기타 현행 안전·상태 진단 및 회귀 검증 스크립트

## 1단계 제거 대상

`tools/refactor_phase1.py`에 있는 `LEGACY_PATHS` 명시 목록을 기준으로 한다. 대상은 구형 `overlay/`, `overlay_workstation/`, 옛 개발용 LeRobot 별도 설치기, 사용되지 않는 중복 런처·MQTT 클라이언트·수동 주문 콘솔·모델 JSON·실행 스크립트이다.

**절대 `robots_ws/src/omx_manipulator/` 전체를 지우지 않는다.** 아직 실제 래퍼와 설치 프로필이 사용 중이다.

## 2단계 이후 (이번 배포에 포함하지 않음)

- `factory.py`, `order_models.py`의 Prefetch 참조를 제거해 원래 `SyncInferenceEngine` 직접 생성으로 복구하고, `act_prefetch.py`와 `set_omx_optimization.sh`의 ON 경로 제거. **별도 동등성 검증 필수**.
- 오래된 README와 Patch 기록을 문서 archive로 이동, 최신 README 작성.
- 사용 안 하는 패치 적용 스크립트와 두 가지 성능 모니터를 정리.
- `main` 병합 여부는 두 브랜치의 차이를 따로 확인한 뒤 결정.

## 장비에 남겨야 하는 로컬 파일

- `~/.config/logistics_omx/device.env`, `profile.env`
- `~/.config/omx/act_prefetch.env` (`OMX_ACT_PREFETCH=0`)
- `~/venv/.venv`, `~/il_ws/src/lerobot/outputs`
- `~/.cache/huggingface/lerobot/calibration`, 저장소의 `state/`

1단계에서 어떤 로컬 파일도 수정·삭제하지 않는다. 물리적 비상정지는 MQTT 타임아웃과 별개다.
