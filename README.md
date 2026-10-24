# Logistics_OMX — OMX1~4 실운영 코드

이 저장소는 4대의 Raspberry Pi 5가 각각 LeRobot ACT 정책으로 로봇팔을 제어하는 운영 코드입니다. 현재 기준 브랜치: `refactor/omx-unified-runtime` (검증 후 배포).

- **OMX1**: A/B 피킹 (모델 매핑은 실행기에서 교차 적용됨)
- **OMX2**: C/D 피킹
- **OMX3**: E 모델 작업대 하차, Persistent Rollout
- **OMX4**: F 모델 작업대 하차, 기본 Wrist-only 정책

## 실제 호출 관계

`omx_start` → `run_omx_mqtt.sh` → `robots_ws/.../runtime/run_omx_pickup.sh` 또는 `run_omx_unload.sh` → `lerobot_unified/run_omx_backend.sh` → (1) `lerobot-rollout` (2) `omx_mqtt_client.py`.

카메라와 모터는 해당 Pi의 로컬 LeRobot이 직접 제어합니다. FMS와 OMX는 MQTT를 사용합니다. 기존 ACT 체크포인트, 가중치, 캘리브레이션과 홈 판정은 변경하지 않습니다.

### 운영 핵심

- **추론:** `SyncInferenceEngine` 고정. 문제 있었던 ACT 비동기 Prefetch는 운영 코드에서 제거되었습니다.
- **주파수:** 30Hz 설정, 새 Action Chunk를 동기 추론하는 동안 실제 제어 공백이 생길 수 있습니다.
- **배포:** 기존 OMX 장치의 설정·모델·venv를 보존하고 Git 브랜치만 순차 배포합니다.
- **완료 판정:** Home 복귀/정지 기반이며 물체를 실제로 넣었는지 증명하지 않습니다.

## 문서 및 검증

- [운영·배포·원복·환경변수](doc/OPERATIONS_CURRENT.md)
- [구조 상세](doc/ARCHITECTURE_CURRENT.md)
- 비구동 검증: `python3 scripts/verify_omx.py --runs 5` 및 `bash scripts/check_omx_release.sh`

### 모터 동작 주의

`omx_start`로 장비를 연결할 때 실제 모터 구동 가능성이 있습니다. 작업 공간·캘리브레이션·물리적 비상정지 수단을 확인하고, 신규 브랜치 첫 시험은 장비 한 대씩 진행하세요.
