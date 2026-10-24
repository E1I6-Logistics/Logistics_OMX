# Logistics_OMX — OMX1~4 운영 및 배포 가이드

> 이 문서는 `refactor/omx-unified-runtime` 정리 브랜치에 대응한다. 로봇 제어 구조와 체크포인트는 기존 `fix/omx-safety-stability` 기준으로 유지한다. 물리적 모터 구동 테스트를 대신하지 않는다.

## 개요

- FMS는 MQTT Broker(`192.168.0.16:1883`)를 통해 각 OMX에 작업을 전달한다.
- 모든 OMX는 Raspberry Pi 5에서 **로컬** 카메라·관절 상태를 읽고, LeRobot Sync ACT 추론 후 OpenRB-150에 관절 목표값을 보낸다.
- 실기에서 이상 동작이 발생한 ACT Prefetch는 제거됐다. RTC 일반 엔진은 LeRobot 라이브러리 구성요소로 남아 있으나 현행 `omx_start`는 Sync 추론을 사용한다.
- CPU 스레드는 기본적으로 온라인 CPU 4개 이하로 설정한다. 장치별 `profile.env`와 레거시 `act_prefetch.env`의 OFF 설정값은 해당 장치에서 유지된다.

## 실제 진입 경로

```
omx_start (scripts/omx_shell.sh)
  └─ run_omx_mqtt.sh
      ├─ ~/.config/logistics_omx/device.env / profile.env
      └─ robots_ws/src/omx_manipulator/runtime/run_omx_{pickup,unload}.sh
          └─ lerobot_unified/run_omx_backend.sh
              ├─ lerobot-rollout → lerobot_unified/src/lerobot/...
              └─ lerobot_unified/omx_mqtt_client.py → MQTT
```

**OMX1/2**: FMS `omxN/command` 수신 → `omx_new_order.sh`로 `state/omx_inventory.json` 기록 → 정책 루프가 주문을 감지 → 지정 ACT 정책 로딩(필요시 교체) → 모터 Action 전송 → Home 복귀 판정과 수량 파일 변경 → MQTT `ack`, `progress`, `result`, `status` 전송. OMX1은 실행기에서 `A` 주문→설정 JSON의 `B` 모델, `B` 주문→`A` 모델을 매핑한다. OMX2는 외부 `C/D` 주문을 내부 `A/B` 슬롯으로 변환한다.

**OMX3/4**: ACT 정책, 카메라, 모터를 한 번 로딩한 뒤 대기(Persistent); MQTT 명령 → 세션별 `state/omxN_workstation_command.json`의 `START` → `READY→RUNNING→DONE→READY` → 결과 전송. 하차 완료는 Home 이탈·복귀 후 정지 판정일 뿐 물품을 실제로 옮겼다는 증거가 아니다.

## 현재 유지하는 설정

- 기본 실행: 모든 Pi의 SSH 대화형 Bash에서 `omx_start`.
- 주문 수신 MQTT: `omxN/command`; 게시 토픽 `omxN/ack`, `omxN/status`, `omxN/progress`, `omxN/result`.
- `lerobot_unified/outputs`는 기존 `~/il_ws/src/lerobot/outputs`의 심볼릭 링크로 준비될 수 있으며 Git에는 가중치가 없다.
- 모델, 캘리브레이션, 카메라 USB by-id, 로봇 ID는 장치별로 다르다. 코드를 정리한다고 이를 복사하거나 재설정하지 않는다.
- `OMX_ACT_PREFETCH=0` 강제. `bash scripts/set_omx_optimization.sh on`은 **오류로 거절**된다. `off`와 `status`만 남긴다.

## GitHub 배포 전 Windows 검증

Windows의 `refactor/omx-unified-runtime`에서:

```powershell
python .\tools\refactor_phase2.py --verify
python .\tools\test_refactor_phase2.py --runs 10
python .\scripts\verify_omx.py --runs 5
git diff --cached --check
git diff --cached --stat
```

이 검증은 문법·정적 제약·합성 소스 변환 확인이며 카메라와 모터를 가동하지 않는다. Python 런타임 의존성이 없는 로컬 검증과 실제 Pi 시스템 검증은 서로 다르다.

## OMX 한 대씩 배포

1. 작업 완료 → 작업자 위치·물리적 정지 확인 → 해당 Pi의 `omx_start`를 종료하고 `pgrep -af '[l]erobot-rollout|[o]mx_mqtt_client.py'`가 비어 있는지 확인한다.
2. 이전 운영 커밋을 기록한다: `cd ~/Logistics_OMX && git rev-parse --short HEAD && git status --short`.
3. `git fetch origin && git switch --track origin/refactor/omx-unified-runtime` (처음 전환할 때만).
4. `bash scripts/check_omx_release.sh`, `python3 scripts/verify_omx.py --runs 5`, `python3 tools/refactor_phase2.py --verify`, `bash scripts/preflight_omx.sh`를 실행한다.
5. `bash scripts/set_omx_optimization.sh status`로 OFF 설정을 확인한다. 새로 추가된 Prefetch를 켜지 말 것.
6. 작업 공간을 비우고 물리적 비상정지 수단을 확보한 뒤 `omx_start`를 시작한다. `SyncInferenceEngine initialized`, MQTT 연결, OMX3/4 `READY` 등 역할별 로그를 확인한다.
7. FMS에서 한 번의 소량 주문을 시험하고 관절 동작·작업 상태·결과를 함께 확인한다. 운영 기준과 차이가 없을 때 다음 Pi를 배포한다.

**권장 순서**: OMX1 → OMX2 → OMX3 → OMX4. 첫 장비에 문제가 생기면 나머지는 기존 운영 브랜치 그대로 둔다.

## 롤백

물리적 정지 확보 → `omx_start` 종료 → 프로세스가 없는지 확인 → 다음을 실행한다.

```bash
cd ~/Logistics_OMX
git switch fix/omx-safety-stability
# 기존 로컬 Prefetch OFF 상태는 유지
bash scripts/set_omx_optimization.sh status
```

`git switch`는 Git이 관리하는 소스만 변경하고, 환경변수 설정이나 모델 가중치·venv·캘리브레이션을 되돌리지는 않는다. 롤백 후 첫 실행도 모터 구동 위험이 있으므로 작업 공간을 확인해야 한다.

## 절대 일괄 삭제하면 안 되는 파일

- `lerobot_unified/src/lerobot/` 및 `lerobot_unified/run_omx_backend.sh`, `omx_mqtt_client.py`, `omx_new_order.sh`
- `robots_ws/src/omx_manipulator/runtime/run_omx_pickup.sh`, `run_omx_unload.sh`, `profiles/`
- `~/.config/logistics_omx/{device,profile}.env`, `~/venv/.venv/`, `~/il_ws/src/lerobot/outputs/`, Hugging Face calibration
- 로컬 `state/`의 주문·작업대 상태를 실행 중에 수동 편집하거나 `git clean -fdx`로 지우지 말 것

## 현재 제한

- Sync ACT는 30Hz 제어 주기 중 Chunk 동기 추론으로 인한 지연이 생길 수 있다.
- Home 완료 판정은 실제 물체 이송 성공을 검증하지 않는다.
- Windows 비구동 테스트는 로봇팔·MQTT·카메라 실기 연결의 안전성을 보장하지 않는다.
