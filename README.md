# Logistics_OMX

Raspberry Pi 5 4GB 한 대에 OMX follower 팔 한 대를 연결해 LeRobot ACT 모델로 피킹·하차를 수행하는 운영 코드입니다. 리더암 없이 기존 모터 보정 JSON, 장비별 YAML, 학습된 모델 복귀 영역을 사용합니다.

**처음에는 이 README를 읽고, 실행·설정·테스트 명령은 [COMMANDS.md — 전체 명령어 사용법](COMMANDS.md)에서 확인하세요.**

- [처음 준비하기](COMMANDS.md#setup)
- [평소 실행·정지·부팅 자동 실행](COMMANDS.md#service)
- [OMX1~4 주문과 단계별 테스트](COMMANDS.md#orders)
- [홈 등록과 모델 사이클 보정](COMMANDS.md#calibration)
- [로그·성능·카메라 보기](COMMANDS.md#diagnostics)
- [에러·복구 대기 대응](COMMANDS.md#recovery)
- [바구니 검사 선택 기능](COMMANDS.md#basket)
- [단축 명령이 없을 때 쓰는 CLI](COMMANDS.md#cli)

## 장비별 역할과 모델

| 장비 | 외부 주문 품목 | 역할 | 모델 입력 카메라 키 |
|---|---|---|---|
| OMX1 | A / B | 물품을 집어 AMR 바구니에 적재 | `wrist`, `front` |
| OMX2 | C / D | 물품을 집어 AMR 바구니에 적재 | `wrist`, `front` |
| OMX3 | 하차 주문의 수량 합계 / 로컬 테스트 E | E 모델로 바구니에서 컨베이어로 하차 | `wrist`, `top` |
| OMX4 | 하차 주문의 수량 합계 / 로컬 테스트 F | F 모델로 바구니에서 컨베이어로 하차 | `top`, `wrist` |

기본 `original` 프로필의 모델 경로는 다음과 같습니다. 최종 실행 값은 각 Pi의 현장 YAML을 따릅니다.

| 장비·외부 키 | 학습 출력 폴더 | 체크포인트 |
|---|---|---|
| OMX1 A | `warehouse_item_b_100_merged_act_200k` | `150000` |
| OMX1 B | `warehouse_all_200_two_cycles_v3_act_250k` | `150000` |
| OMX2 C | `warehouse_item_c_160_merged_basket_fix_v1_act_200k` | `170000` |
| OMX2 D | `warehouse_item_d_190_merged_act_200k` | `170000` |
| OMX3 E | `warehouse_item_e_132_merged_act_300k` | `250000` |
| OMX4 F | `warehouse_item_f_v3_5_act_300k` | `230000` |

모델 경로는 보통 `lerobot_unified/outputs/train/<학습 폴더>/checkpoints/<번호>/pretrained_model`입니다. **OMX1의 외부 A/B와 모델 이름이 반대인 매핑은 현장에서 확인한 값입니다.** 이름만 보고 뒤집지 마세요. OMX3·4는 FMS의 A~D 품목별 수량도 합산해 E/F 모델의 동작 횟수로 사용할 수 있습니다.

## 현재 운영 방식

### 주문과 카운팅

- OMX1·2는 MQTT 주문의 품목별 모델을 선택하고, 등록된 사이클 이탈·복귀 조건으로 완료 횟수를 셉니다.
- OMX3·4는 바구니 검사를 끄면 **요청한 1~8회 동작 사이클만 수행**합니다. `omx_unload 2`는 정상 사이클 2회 후 종료입니다.
- 검사 OFF에서는 수량 없는 주문과 인자 없는 `omx_unload`를 거절합니다. 자동으로 무한 반복하지 않습니다.
- 완료 사이클은 실제 물건 개수나 전달 성공을 검증한 값이 아닙니다. 모델이 헛집기해도 정상 이탈·복귀하면 사이클로 기록될 수 있습니다. 움직이지 않거나 복귀하지 못하면 제한 시간과 판정 조건에 따라 실패합니다.

이번 현장 운영은 OMX3·4의 `unload.inspection_enabled: false`를 사용합니다. 저장소의 `params/omx3.yaml`, `params/omx4.yaml` **템플릿 기본값은 true**이므로 새 장비에서는 `omx_basket off`를 한 번 저장하고 재시작해야 합니다. 설정은 Git이 추적하지 않는 현장 YAML에 보존됩니다.

### 실행과 복구

등록한 사용자 systemd 서비스가 부팅 후 실행기를 시작합니다. 모델·카메라 연결과 홈 확인 후 MQTT 주문을 기다리며, 감독 프로그램이 비정상 종료되면 서비스를 다시 시작합니다. `omx_service stop`으로 사용자가 정지하면 즉시 재시작하지 않습니다.

전원 차단·강제 종료 후에는 장치와 중단 주문을 재점검합니다. 이전 주문을 자동으로 재실행하지 않습니다. 팔이 검증된 복귀 범위 밖에 있거나 카메라 원래 설정 복원이 확인되지 않으면 `RECOVERY_WAIT`로 대기할 수 있습니다. 서비스의 `active (running)`과 로봇의 주문 수신 준비 완료는 다르므로 **홈 도착·READY/주문 대기·MQTT 연결 로그**까지 확인합니다.

## 빠르게 사용하기

이미 장비 등록·모델·홈·사이클 보정이 완료된 Pi 기준입니다.

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service status
omx_service logs --follow
```

로그 보기에서 Ctrl+C를 눌러도 서비스는 계속 실행됩니다. 이후 다른 터미널에서 해당 장비 명령을 실행합니다.

| 현재 장비 | 물품 하나로 첫 시험 |
|---|---|
| OMX1 | `omx_order A 1` 또는 `omx_order B 1` |
| OMX2 | `omx_order C 1` 또는 `omx_order D 1` |
| OMX3 / OMX4 (검사 OFF) | `omx_unload 1` |

위 예시는 장비별 선택 명령입니다. OMX1에서 C 주문을 보내거나 OMX2에서 A 주문을 보내지 않습니다. 각 주문 완료를 확인한 뒤 다음 주문을 보냅니다. [설치부터 전체 테스트 순서까지](COMMANDS.md#orders)

## 설정과 실행 구조

```text
params/omx1.yaml ... omx4.yaml        장비별 배포 템플릿
state/config/omxN.yaml               현장 설정·홈·보정·모델 경로 (Git 제외)
~/.config/logistics_omx/shell.env     장비 번호·YAML·Python 단축 명령 등록
state/manager/omxN/                  주문·복구·사이클·성능 상태
logs/omxN/                          전체·중요 로그와 순환 보관 파일

systemd 사용자 서비스 또는 omx_start
  -> run_omx_mqtt.sh
  -> omx_control -> omx_manager.cli / runtime.Supervisor
       ├─ lerobot.scripts.lerobot_rollout   ACT 추론·카메라·모터
       └─ lerobot_unified/omx_mqtt_client.py   MQTT 주문·응답
```

현장 설정은 `omx_info`에 표시되는 `OMX_CONFIG_PATH`가 기준입니다. `params/` 템플릿을 수정해도 이미 존재하는 현장 설정이 자동으로 덮어써지지 않습니다. 파라미터에는 한글 주석이 붙으며, 보통 설정 저장 후 실행기를 다시 시작해야 적용됩니다.

모델·정밀도 설정도 YAML에서 관리하지만 현재 실행 백엔드는 `lerobot_pytorch`, 정밀도는 **FP32**입니다. `int8_candidate`는 미구현 후보 설정이며 경로만 넣어 양자화 모델을 실행할 수 있는 상태가 아닙니다. 원본 모터 보정 JSON은 홈 등록으로 재보정하지 않습니다.

## 환경과 준비물

- Linux / Raspberry Pi 5 4GB, Python 3.12 기반의 장비별 동작 확인된 가상환경.
- OpenRB-150, 6개 Dynamixel 관절, 모델이 학습한 상단·손목 RGB 카메라 입력.
- MQTT 브로커와 같은 네트워크. 기본 예시는 `192.168.0.16:1883`입니다.
- 장비에 맞는 ACT 체크포인트·전후처리 상태 파일과 기존 모터 보정 JSON.

**모델 가중치·현장 YAML·홈 등록·모터 보정 파일은 Git clone만으로 준비되지 않습니다.** 기존 Pi에서는 동작 중인 가상환경과 로컬 자료를 보존하세요. 새 장비의 포트·카메라 시리얼·모델·보정 경로는 [최초 등록 절차](COMMANDS.md#setup)대로 설정합니다. `requirements.txt`와 `requirements/`는 환경 참고 자료이며 다른 Pi의 전체 패키지 목록을 무조건 덮어쓰는 설치 지침이 아닙니다.

ACT는 CPU 동기 추론을 사용합니다. 카메라 30fps와 YAML의 30Hz 설정이 실제 추론·제어 30Hz를 보장하지는 않습니다. 새 행동 청크를 추론하는 동안 제어 공백이 생길 수 있으며, CPU 4스레드 설정도 네 코어의 상시 100% 사용을 뜻하지 않습니다.

## MQTT 인터페이스

| 토픽 | 방향 | 내용 |
|---|---|---|
| `omxN/command` | FMS → OMX | `job_id`, 품목별 수량 `items` |
| `omxN/ack` | OMX → FMS | 주문 접수 여부 |
| `omxN/progress` | OMX → FMS | `current`, `total`, 사이클 진행 등 |
| `omxN/result` | OMX → FMS | 주문 ID, 성공/실패, 메시지 |
| `omxN/status` | OMX → FMS | IDLE / WORKING / ERROR 등 |

OMX3·4 검사 OFF 예시:

```json
{"job_id":"order-001","items":{"A":2,"C":1}}
```

수량 합계인 3회 사이클을 수행합니다. 완료 결과의 `count_kind`는 `motion_cycles`, `inspection_scope`는 `disabled`, `basket_empty_verified`와 `physical_unloading_verified`는 false입니다. `success: true`는 요청한 사이클 수 완료를 뜻합니다. [CLI와 직접 MQTT 테스트](COMMANDS.md#orders)

## 작업 환경 예시

기존 main의 작업 환경 이미지를 보존했습니다. 사진이 현재 모든 Pi의 카메라 위치·현장 배치를 보증하지는 않습니다.

| A/B 피킹 | C/D 피킹 |
|---|---|
| <img src="doc/images/ab_model.png" alt="A/B 작업 환경" width="420" height="315"> | <img src="doc/images/cd_model.png" alt="C/D 작업 환경" width="420" height="315"> |

| E 하차 | F 하차 |
|---|---|
| <img src="doc/images/e_model.png" alt="E 작업 환경" width="420" height="315"> | <img src="doc/images/f_model.png" alt="F 작업 환경" width="420" height="315"> |

## 문서와 개발

- **현재 전체 명령어:** [COMMANDS.md](COMMANDS.md)
- **세부 설명·이전 변경 기록:** [OMX_사용설명서.md](OMX_사용설명서.md). 검사 OFF 운영에 바구니 등록 절차를 적용할 필요는 없습니다.
- 코드: [관리 실행기](omx_manager/runtime.py), [홈·사이클 연결](omx_manager/hooks.py), [검사 없는 하차](omx_manager/workstation_cycles.py), [MQTT](lerobot_unified/omx_mqtt_client.py), [서비스 설치](omx_manager/service.py).
- `main`은 배포 기준, `dev`는 개발·검증 브랜치입니다. 실행 방법은 두 브랜치에 동일하게 문서화합니다.
- 구형 `profile.env`·overlay 런처를 설명하는 자료는 이 README의 현재 실행 경로와 구분하세요. 일반 운영은 `omx_service` / `omx_start`와 현장 YAML을 사용합니다.

가상 검증은 실제 로봇을 연결하지 않는 개발 환경에서 실행합니다. 새 문서·Git 통합 검증과 실물 시험 결과는 구분합니다. [검증 명령과 브랜치 반영 방법](COMMANDS.md#development)
