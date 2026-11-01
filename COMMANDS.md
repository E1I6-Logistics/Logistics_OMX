# OMX 명령어 사용법

[README로 돌아가기](README.md)

현재 `omx_manager`, `scripts/omx_shell.sh`, `omx_manager.cli` 기준입니다. 일반 명령은 **해당 팔의 Pi에서, 그 장비 사용자로** 실행합니다. 서비스 이름·설정 경로는 `omx_info`의 등록값을 사용합니다. Windows PowerShell 명령은 별도 표시했습니다.

## 목차

1. [Git 받기·최초 등록·업데이트](#setup)
2. [평소 실행·정지·부팅 자동 실행](#service)
3. [OMX1~4 주문·실물 테스트·직접 MQTT](#orders)
4. [홈·모델 사이클 보정·복귀 경로](#calibration)
5. [YAML과 모델 설정](#configuration)
6. [로그·성능·카메라 보기](#diagnostics)
7. [에러·복구 대기](#recovery)
8. [선택 기능: 바구니 검사](#basket)
9. [단축 명령 없이 사용하는 CLI 전체 목록](#cli)
10. [개발 검증·dev/main·서브모듈](#development)

<a id="setup"></a>
## 1. Git 받기·최초 등록·업데이트

### 처음 코드를 받는 장비

가중치와 모터 보정, 동작 확인된 Python 환경은 별도로 준비되어 있어야 합니다. 이미 운영 중인 `~/Logistics_OMX`가 있으면 다시 clone하지 않고 아래 업데이트 절차를 사용합니다.

```bash
git clone --branch main https://github.com/E1I6-Logistics/Logistics_OMX.git ~/Logistics_OMX
cd ~/Logistics_OMX
# 개발 브랜치가 필요하면: git switch dev
```

동작 확인된 가상환경 Python을 지정하고 단축 명령을 등록합니다. `--robot-id`는 실제 팔 번호로 바꾸세요.

```bash
cd ~/Logistics_OMX
bash scripts/install_omx_shell.sh --robot-id omx3 --python "$HOME/venv/.venv/bin/python"
source ~/.bashrc
omx_info
omx_help
```

OMX1→`omx1`, OMX2→`omx2`, OMX3→`omx3`, OMX4→`omx4`입니다. 등록기는 `state/config/omxN.yaml`이 없을 때만 템플릿을 복사하고 기존 현장 설정을 보존합니다. 이 등록 명령 자체는 로봇·서비스를 시작하거나 패키지를 설치하지 않습니다. 호스트명·기존 등록이 일치하면 `--robot-id auto`도 사용할 수 있습니다.

```bash
nano "$OMX_CONFIG_PATH"
ls -l /dev/serial/by-id/
ls -l /dev/v4l/by-id/
```

`hardware.port`, `hardware.robot_id`, `hardware.calibration_file`, 카메라 경로·시리얼·입력 키와 해상도, 모델 경로, MQTT 주소를 실제 장비에 맞춥니다. `SET_OMX...`는 현장에서 채워야 하는 값입니다. 모델 입력은 학습 때의 카메라 키·해상도와 같아야 합니다. 상세 설정은 [5절](#configuration)을 참고합니다.

### 이미 운영 중인 장비 업데이트

주문이 없는 상태에서 서비스를 정지합니다. 전경 `omx_start`를 사용 중이면 그 터미널에서 Ctrl+C로 정상 종료합니다.

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service stop
mkdir -p state/backups
cp "$OMX_CONFIG_PATH" "state/backups/${OMX_ROBOT_ID}-$(date +%Y%m%d-%H%M%S).yaml"
git status --short
git pull --ff-only
source ~/.bashrc
omx_check
omx_workflow
omx_service start
```

서비스를 아직 설치하지 않은 장비는 stop/start 대신 전경 실행을 사용합니다. `git pull --ff-only`는 현재 브랜치의 upstream을 갱신합니다. 추적 설정이 없으면 현재 브랜치에 맞게 한 번 설정합니다. 예: `git branch --set-upstream-to=origin/dev dev` 또는 main에서 `git branch --set-upstream-to=origin/main main`.

설치된 서비스 파일이 바뀐 업데이트에서는 start 전에 `omx_service install`, `omx_service enable`도 수행합니다. 현장 YAML은 `state/`에 있으므로 업데이트 뒤 템플릿으로 다시 복사하지 않습니다. `git status`에 사용자 수정이 있거나 fast-forward가 거절되면 reset/강제 pull로 덮어쓰지 않고 차이를 확인합니다.

<a id="service"></a>
## 2. 평소 실행·정지·부팅 자동 실행

### 자주 쓰는 명령

| 명령 | 동작 |
|---|---|
| `omx_service status` | 현재 서비스 상태. `enabled`는 자동 실행 등록, `active (running)`은 감독 프로그램 실행 |
| `omx_service start` | 서비스 시작. 모델·카메라 연결과 홈 확인 후 주문 대기 |
| `omx_service stop` | 현재 서비스 정지. 사용자 정지를 즉시 자동 재시작하지 않음 |
| `omx_service logs` | 최근 서비스 로그 50줄 |
| `omx_service logs --follow` | 서비스 로그 계속 보기. Ctrl+C는 로그 보기만 종료 |
| `omx_service disable` | 다음 부팅 자동 실행 해제. 현재 실행까지 끄려면 stop도 수행 |
| `omx_service uninstall` | 생성한 서비스를 정지·자동 실행 해제 후 제거 |
| `omx_start --diagnostics` | 서비스 대신 터미널 전경 실행. Ctrl+C는 실행기 종료 |

서비스와 전경 실행을 동시에 켜지 않습니다. 단독 모터 명령·보정·카메라 단독 점유 작업 전에는 서비스를 정지합니다. MQTT 주문 명령, 로그·모니터, 공유 뷰어는 실행 중인 서비스를 대상으로 사용합니다.

### 부팅 자동 실행을 처음 등록할 때 — 각 Pi 한 번

홈·사이클 보정과 소량 동작 시험을 완료한 장비에서 실행합니다.

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service install
sudo loginctl enable-linger "$USER"
omx_service enable
omx_service start
omx_service status
omx_service logs --follow
```

설치만 하면 실행되지 않으며 기본 설정에서는 자동 시작도 켜지지 않습니다. enable과 start가 각각 필요합니다. linger는 로그인 전에도 사용자 서비스를 시작하고 로그아웃 후에도 유지하는 설정입니다. 실행기는 등록된 YAML과 Python을 읽으므로 로그인 셸을 열 필요가 없습니다.

준비 로그는 `HOME ARRIVED`, `Base strategy ready`, OMX3·4의 `[WORKSTATION] READY`, `[MQTT] Connected: 0` 등입니다. OMX1·2는 주문이 없으면 홈에서 대기합니다. `Inventory empty` 로그가 있어도 실제 바구니를 카메라로 검증한 결과는 아닙니다.

작업이 없는 대기 상태에서 재부팅 검증:

```bash
sudo reboot
# 재접속한 뒤 omx_start를 따로 실행하지 않고 확인
omx_service status
omx_service logs --follow
```

기본 서비스 재시작 대기는 5초입니다. 감독 프로세스 재실행과 Pi OS 재부팅은 다릅니다. 장치·카메라 복원이나 중단 주문 확인이 필요하면 프로세스는 살아 있어도 복구 대기일 수 있습니다. [7절](#recovery)

<a id="cold-start"></a>
### OpenRB·모터 전원도 껐다 켜는 장비 — 선택 설정

Pi만 재부팅하는 경우와 달리 모터 전원 재인가 후에는 토크가 OFF일 수 있습니다. 기존 설정과 템플릿은 `recovery.startup_arm_at_home: false`로 수동 `omx_arm`을 사용합니다. 다음 설정은 **등록 홈에 정지해 있는 장비에서 시작 시 토크 ON을 허용**합니다. 먼저 물품을 확인하고 팔을 등록 홈에 배치해 작업 공간을 비웁니다. 홈·모델·사이클 보정을 다시 등록할 필요는 없습니다.

각 Pi에서 한 번:

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service stop
git pull --ff-only
./omx_control --config "$OMX_CONFIG_PATH" set --key recovery.startup_arm_at_home --value true
./omx_control --config "$OMX_CONFIG_PATH" validate
omx_service start
omx_service logs --follow
```

이 변경은 서비스 재설치가 필요하지 않습니다. 토크 ON 조건은 전체 6축 토크 OFF, 정상 하드웨어 상태·기존 보정·모드 일치, 등록 홈 오차 이내, 연속 위치 샘플의 정지 확인입니다. `startup_arm_tolerance` 기본값은 **2.0 정규화 단위(각도 아님)**이며 최대 3.0, `home.max_start_error` 이내입니다. 일반 홈·카운트 허용 오차를 넓히는 값이 아닙니다. 기존 모터 모드·PID·EEPROM·보정은 변경하지 않습니다.

현재 위치를 6축 목표에 먼저 저장하고 토크를 켠 뒤 토크·위치·목표 피드백을 재확인합니다. 작은 홈 오차는 기존 제한된 홈 복귀로 정리하며, 적재 여부를 모르는 그리퍼는 자동으로 열지 않습니다. 성공 로그는 `[RECOVERY] [STARTUP] Torque enabled at registered Home`이며, 이후 `HOME ARRIVED`·준비·MQTT 연결까지 확인합니다. 운영을 시작한 뒤의 토크 OFF나 일부 관절만 OFF인 상태는 이 설정으로 자동 재활성화하지 않습니다.

토크 ON 검증 실패·강제 종료 중 미확인 시도는 `startup_arm.json`에 남겨 실행기·OS를 다시 시작해도 자동 반복하지 않습니다. 실제 상태를 확인한 뒤 실행 중인 복구 대기는 `omx_resume`, 실행기를 멈춘 상태에서는 `omx_recover`로 기록을 보관·정리합니다. 이전 물리 주문은 재실행하지 않습니다. 실패 때 토크를 강제로 끄지 않으므로 팔 지지가 필요할 수 있습니다.

첫 실물 검증은 주문 없는 상태에서 서비스 정지 후 Pi·OpenRB·모터 전원을 정상 종료·재인가하고, 홈에 유지된 팔에서 별도 `omx_start` 없이 준비되는지 확인합니다. 이전 부팅 로그와 구분하려면:

```bash
journalctl --user -u "omx-manager@${OMX_ROBOT_ID}.service" -b -n 60 --no-pager
cat "state/manager/$OMX_ROBOT_ID/startup_arm.json"
```

`Input/output error` 같은 USB/모터 통신 오류는 이 옵션으로 해결되지 않습니다. 서비스를 멈추고 `omx_motor_check`로 연결을 확인합니다. 수동 방식으로 되돌리려면 서비스 정지 후 같은 set 명령의 값을 `false`로 저장하고 재시작합니다. 가상 시험은 실제 전원 재인가·토크 동작 검증을 대신하지 않습니다.

<a id="automatic-recovery"></a>
### 재시작 때 기존 작업 초기화·오류별 자동 복구 — OMX1~4 공통 선택

`restart_to_idle`을 켜면 실행기/전원 재시작 뒤 RUNNING 주문을 INTERRUPTED·실패로 남기고 활성 IPC·주문 카운터를 초기화합니다. 이전 진행 수치·명령은 `state/manager/omxN/archive/restart_<ID>/`에 보관합니다. `jobs.json`의 주문 ID·서명·완료/실패 기록은 유지하므로 FMS가 같은 주문을 재전송해도 다시 동작하지 않습니다. 홈·모터 보정·모델·카메라 설정은 초기화하지 않으며 실제 물건 적재 상태는 여전히 미검증입니다. 현장 YAML의 `interrupted_order_action: require_ack`를 선택한 경우 수동 중단 주문 정책은 유지합니다.

팔·물품을 확인하고 등록 홈에 둔 상태에서 각 Pi에 적용합니다:

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service stop
git pull --ff-only
./omx_control --config "$OMX_CONFIG_PATH" set --key recovery.startup_arm_at_home --value true
./omx_control --config "$OMX_CONFIG_PATH" set --key recovery.restart_to_idle --value true
omx_service start
omx_service logs --follow
```

서비스 재설치는 필요하지 않습니다. 각 팔 내부 준비 순서는 `[STATE] RECONCILING` → `MOTOR_CHECK` → 필요 시 `HOME_RETURN` → `HOME_VERIFIED` → `MODEL_LOADING` → `MQTT_CONNECTING` → `READY`입니다. MQTT 프로세스가 실제 연결을 보고한 뒤에만 READY로 표시합니다. 서로 다른 Pi 4대를 전역적으로 1→2→3→4 순서로 기동하는 기능은 아닙니다.

| 오류/상태 | 자동 처리 범위 |
|---|---|
| 중단 주문 | 실패·진행 증거 보관, 이전 주문 재실행 없이 새 대기 준비 |
| USB/통신, 카메라, heartbeat 지연, MQTT/모델 프로세스 종료, 메모리 오류 | 기존 자식 프로세스 정리 후 장치·홈 연속 2회 점검, 통과 시 새 실행 세션 |
| 카메라 노출 복원 pending | 별도 카메라 전용 자식에서 원래 설정 복원 1회. 시간 제한·종료·회수 확인 후에만 다음 단계. 복원 실패는 대기 |
| 모터 하드웨어 오류·모드/보정 불일치·토크 OFF·홈 추종 실패·알 수 없는 오류 | 자동 토크 재활성화·모드/PID 수정·오류 삭제 없이 복구 대기 |
| 검증된 홈 접근 영역/복귀 경로 밖 | 임의 자세에서 홈까지 직선 이동하지 않음. 현장 배치 또는 검증된 경로 필요 |
| 사용자 stop/Ctrl+C | 자원 정리 후 정지. 자동 모델 재실행이나 복귀 이동 없음 |

시작 토크 ON은 앞 절의 별도 조건에서만 허용됩니다. 운영 중 토크 OFF를 자동으로 다시 켜지 않으며, 미확인 물품을 놓기 위해 그리퍼를 자동으로 열지 않습니다. `startup_arm.json`의 실패/미완료 기록도 자동 삭제하지 않습니다.

자동 실행 재시도는 **같은 Pi 부팅 동안 기본 3회**, `recovery.auto_retry_limit`로 1~5회 설정할 수 있습니다. 실행기·서비스만 재시작해서 상한을 우회하지 않습니다. 상한 도달 후 진단·복구 MQTT를 유지하며 대기하고, 실제 상태를 확인한 `omx_resume`/실행기 정지 후 `omx_recover`로 기록을 보관·재점검합니다. 새 OS 부팅에서는 재시도 예산을 새로 계산하지만 물리 오류·시작 토크 검증 실패는 계속 차단합니다. **Pi OS를 오류마다 자동 재부팅하거나 OpenRB·모터 전원을 자동 차단하는 기능은 아닙니다.**

```bash
cat "state/manager/$OMX_ROBOT_ID/supervisor_state.json"
cat "state/manager/$OMX_ROBOT_ID/recovery_wait.json"
cat "state/manager/$OMX_ROBOT_ID/auto_restart.json"
```

실물 시험은 빈 작업 공간에서 주문 없이 정상 종료·전원 재인가 후 READY를 확인하고, 품목 1개 주문을 연속 2회 진행합니다. 오류 복구 강제 시험은 실물 팔 대신 개발 환경의 `tests/test_restart_policy.py`를 사용합니다. 가상 시험은 충돌 없는 복귀 경로나 물건 전달 성공을 검증하지 않습니다.

<a id="orders"></a>
## 3. OMX1~4 주문·실물 테스트·직접 MQTT

각 장비가 홈에서 주문 수신 준비를 끝낸 후 **별도 SSH 터미널**에서 전송합니다. 테스트할 물품과 공간을 준비하고 팔별로 하나씩 확인합니다. 아래 명령은 실제 모델 동작을 시작합니다. 완료 응답 뒤에 다음 주문을 보냅니다.

| 장비 | 첫 시험 | 다음 품목/반복 시험 |
|---|---|---|
| OMX1 | `omx_order A 1` | `omx_order B 1`, 이후 `omx_order A 2` |
| OMX2 | `omx_order C 1` | `omx_order D 1`, 이후 `omx_order C 2` |
| OMX3 | `omx_unload 1` | `omx_unload 2` → 완료 후 다시 `omx_unload 1` |
| OMX4 | `omx_unload 1` | `omx_unload 2` → 완료 후 다시 `omx_unload 1` |

OMX3·4의 위 시험은 **검사 OFF** 기준입니다. 최초 한 번, 실행기를 멈춘 상태에서:

```bash
omx_basket off
omx_service start  # 서비스를 사용한다면
# 서비스 대신 전경 실행을 선택한 경우: omx_start --diagnostics
```

`omx_unload 2`는 실제 물품 두 개를 보증하는 명령이 아니라 **정상 이탈·복귀 사이클 2회** 요청입니다. 출력의 `motion cycles=1/2`, `motion cycles=2/2`와 MQTT `result`의 `success: true`를 확인합니다. 다음 주문에서는 새로 `1/1`이 되어야 합니다. 모델이 움찔만 하거나 돌아오지 못하면 완료로 세지 않고 제한 시간 후 실패할 수 있습니다.

단축 주문의 수량은 1~8 정수입니다. `omx_order A`처럼 수량을 생략하면 1입니다. 검사 OFF 상태의 인자 없는 `omx_unload`는 거절됩니다. 서비스는 계속 켜 둔 채 주문만 전송하면 됩니다.

### FMS와 같은 MQTT 명령으로 시험

`omx_order`와 `omx_unload`도 내부에서 `mosquitto_pub`로 MQTT를 전송합니다. 명령이 없으면 Pi에서 `sudo apt install mosquitto-clients`로 준비합니다.

아래는 Pi/Linux 셸 예시이며 FMS를 동시에 자동 운행시키지 않는 수동 시험용입니다. `OMX_ROBOT_ID`는 등록한 현재 장비 ID, 브로커 주소는 현장 YAML의 값에 맞춥니다. 응답은 전송 **전** 별도 터미널에서 구독합니다.

```bash
mosquitto_sub -h 192.168.0.16 -p 1883 -t "$OMX_ROBOT_ID/#" -v
```

OMX1 A 한 개 예시(OMX1에서):

```bash
mosquitto_pub -h 192.168.0.16 -p 1883 -t omx1/command -q 1 \
  -m "{\"job_id\":\"cli-$(date +%s%N)\",\"items\":{\"A\":1}}"
```

OMX3·4 검사 OFF에서 수량 합계 세 번 요청(해당 Pi에서):

```bash
mosquitto_pub -h 192.168.0.16 -p 1883 -t "$OMX_ROBOT_ID/command" -q 1 \
  -m "{\"job_id\":\"cli-$(date +%s%N)\",\"items\":{\"A\":2,\"C\":1}}"
```

FMS는 `job_id`와 `items`를 같은 구조로 보내면 됩니다. OMX1은 A/B, OMX2는 C/D만 처리합니다. OMX3·4의 하차 MQTT 검증은 A~F 키를 허용하며 수량 합계 1~8을 동작 횟수로 사용합니다. 로컬 `omx_unload`는 OMX3에서 E, OMX4에서 F 키로 같은 수량을 전송합니다. 새 작업마다 새 job_id를 사용하며 완료 전에는 다음 작업을 보내지 않습니다. retained 명령은 사용하지 않습니다.

| 응답 토픽 | 확인할 내용 |
|---|---|
| `omxN/ack` | `accepted: true`는 접수이며 완료가 아님 |
| `omxN/progress` | `current`, `total`, `completed_cycles` 등 |
| `omxN/result` | 같은 `job_id`, `success`, `message` |
| `omxN/status` | `IDLE`, `WORKING`, `ERROR` 등 |

검사 OFF 완료 이유는 내부 상태의 `requested_cycles_completed`입니다. 결과에는 `count_kind: motion_cycles`, `inspection_scope: disabled`, `basket_empty_verified: false`, `physical_unloading_verified: false`가 포함됩니다. success는 동작 횟수 완료이며 실제 물품 전달 검증을 뜻하지 않습니다.

<a id="calibration"></a>
## 4. 홈·모델 사이클 보정·복귀 경로

정상 동작하던 장비의 업데이트·일상 실행에는 재등록이 필요하지 않습니다. 단독 관절 접근 명령은 먼저 서비스를 stop하고 사용합니다. 리더암을 연결하는 절차는 없습니다.

| 명령 | 의미 |
|---|---|
| `omx_observe` | 현재 관절 위치와 등록 홈 오차 관측 |
| `omx_motor_check` | 목표·현재 위치·토크·모드·오류·게인 등을 약 3초 관측. 모터 쓰기 없음 |
| `omx_capture` | **현재 자세를 새 홈으로 저장**. 홈으로 이동시키는 명령이 아님 |
| `omx_arm` | 현재 자세를 목표로 설정하고 토크 ON. 허용 접근 영역 확인 필요 |
| `omx_home` | 등록 홈 근처에서 제한 속도·추종 오차·도착 조건으로 복귀 |
| `omx_release` | 기본 10초 대기 후 전체 토크 OFF. 물품을 내려놓고 팔을 지지한 뒤 사용 |
| `omx_calibrate` | 관절 관측·홈·설정·가상 시험 등을 선택하는 메뉴 |
| `omx_setup` | 기존 YAML에 부족한 관리 설정 추가. 모델·홈·모터 보정을 새로 학습하지 않음 |
| `omx_tune` | CPU 4스레드·cycle 카운팅·검사 기본 주기를 설정. 만능 성능 개선이나 모델 양자화 명령이 아님 |
| `omx_workflow` | 모델 복귀 프로필 확인. 검사 ON인 OMX3·4에서는 바구니 기준도 확인 |

### 홈을 새로 등록해야 하는 경우

실제 팔을 모델의 올바른 초기 자세로 맞추고 정지시킨 뒤 저장합니다. 토크를 끄고 자세를 맞출 필요가 있으면 팔을 지지하고 release를 사용합니다. 움직이는 팔을 잡아 강제로 등록하지 않습니다.

```bash
omx_service stop
omx_observe
# 현장에서 모델의 올바른 초기 자세로 맞춘 뒤
omx_capture
omx_arm
omx_home
omx_check
```

홈 변경으로 기존 모델 복귀 프로필·복귀 경로·검사 기준의 바인딩이 달라질 수 있습니다. `omx_workflow`에서 요구하는 해당 보정을 다시 진행합니다. `omx_home`이 접근 범위 밖이라고 거절하면 허용 오차를 크게 늘려 임의로 뻗은 자세를 강제 복귀시키지 않습니다.

### 모델을 실행해 카운팅용 복귀 영역 등록

고정 초기 홈과 모델이 돌아오는 복귀 영역은 별개입니다. 모델별로 정상 집기·놓기·복귀를 직접 확인하며 기본 3회 보정합니다. pick 자세나 place 자세를 수동으로 여러 곳 등록할 필요는 없습니다.

```bash
# 해당 장비에서만 선택 실행
omx_cycle_calibrate A  # OMX1: A와 B 각각
omx_cycle_calibrate C  # OMX2: C와 D 각각
omx_cycle_calibrate E  # OMX3
omx_cycle_calibrate F  # OMX4
omx_workflow
```

화면의 준비 안내 후 Enter로 시작하고 실제 정상 사이클이 끝난 뒤 확인합니다. 헛집기·미완료는 q로 취소합니다. 보정 명령은 실제 모델을 구동합니다. `omx_capture`만으로 모델 복귀 보정이 완료되는 것은 아닙니다.

### 선택: 검증된 복귀 경로

| 명령 | 의미 |
|---|---|
| `omx_route fold` | 현재 자세를 fold 경로의 다음 경유점으로 등록. 모터 이동 없음 |
| `omx_route_test fold` | 엄격한 홈에서 시작해 기록 경로 왕복 시험 후 승인. 실제 이동 |
| `omx_return_home` | 홈 접근 영역 또는 승인된 경로로 복귀. 실제 이동 |
| `omx_stage E pick` | 이전 `ordered_stages` 모드의 단계 등록. 현재 cycle 모드에서는 필수 아님 |

경유점은 현장에서 실제 검증할 수 있는 순서로 기록하고 마지막 홈까지 포함해야 합니다. 승인 범위를 벗어난 임의 자세의 자동 복귀 경로를 생성하지 않습니다. 자세·경로를 변경한 뒤에는 다시 시험해야 합니다.

<a id="configuration"></a>
## 5. YAML과 모델 설정

```bash
omx_info
nano "$OMX_CONFIG_PATH"
```

| 설정 | 용도 |
|---|---|
| `hardware.port`, `hardware.robot_id`, `hardware.calibration_file` | 보드·모터 보정 식별 |
| `hardware.cameras` | 모델 입력 이름, 카메라 종류·시리얼·해상도 |
| `models.active_profile`, `models.profiles` | 사용할 모델·태스크·프로필 |
| `runtime.broker_host`, `runtime.broker_port` | MQTT 브로커 |
| `runtime.cpu_threads`, `runtime.fps` | CPU 스레드와 목표 제어 주기 |
| `home`, `counting` | 고정 홈과 모델 복귀 카운팅 |
| `unload.inspection_enabled` | OMX3·4 바구니 검사 ON/OFF |
| `diagnostics` | 성능 기록·로그 설정 |
| `service` | 서비스 재시작 간격·종료 대기 |
| `recovery.startup_arm_at_home`, `recovery.startup_arm_tolerance` | 시작 시 홈 근처 토크 ON 허용 여부(기본 false), 허용 오차(기본 2.0 정규화 단위) |
| `recovery.restart_to_idle`, `recovery.auto_retry_limit` | 중단 작업 정리·오류별 제한 자동 복구(기본 false), 부팅당 자동 재시도 상한(기본 3) |
| `view` | 읽기 전용 뷰어의 접속 IP·포트·주기 |

간단한 값은 공통 CLI로 저장할 수 있습니다. 장비·모델 경로처럼 전체 검토가 필요한 필드는 YAML에서 편집합니다.

```bash
./omx_control --config "$OMX_CONFIG_PATH" set --key runtime.cpu_threads --value 4
./omx_control --config "$OMX_CONFIG_PATH" set --key view.allow_ip --value '192.168.0.19'
./omx_control --config "$OMX_CONFIG_PATH" validate
./omx_control --config "$OMX_CONFIG_PATH" plan
omx_check
omx_workflow
```

설정 변경 전 실행기를 멈추고, 검사 후 다시 시작합니다. `validate`는 YAML 구조, `plan`은 생성할 실행 인자를 확인하며 실제 모터 통신을 시험하지 않습니다. `omx_check`도 파일·모델 I/O·보정·홈 바인딩 검사이며 실제 구동 성공을 뜻하지 않습니다. 모터는 `omx_observe` / `omx_motor_check`로 별도 확인합니다.

모델 변경 시 `pretrained_model`에 config·가중치·전후처리 설정과 각 설정에서 참조하는 상태 파일이 필요합니다. 카메라 키·해상도와 6축 ACT 출력을 검사합니다. 변경 후 기존 홈·복귀 보정의 유효성을 재확인합니다. `int8_candidate`의 validated를 켜거나 precision을 바꾸는 것만으로 현재 실행기가 INT8을 지원하지는 않습니다.

<a id="diagnostics"></a>
## 6. 로그·성능·카메라 보기

| 명령 | 용도 |
|---|---|
| `omx_service logs --follow` | 서비스 시작·종료·복구 로그 계속 보기 |
| `omx_logs --follow` | 관리 실행기가 보관하는 중요 로그 |
| `omx_logs --lines 100` | 최근 중요 로그 100줄 |
| `omx_status` | 상태와 성능 한 번 조회 |
| `omx_monitor` | CPU·메모리·온도·카메라·제어/청크 상태 계속 보기 |
| `omx_monitor --json --samples 3` | 상태 JSON 3회 조회 |
| `omx_info` | 장비 번호·현장 YAML·Python·저장소 경로 |
| `omx_help` | 현재 단축 명령 도움말 |
| `omx_cd` | 등록한 OMX 저장소로 이동 |

상세 성능 수집은 YAML의 `diagnostics.enabled: true`를 저장하고 재시작하거나, 전경에서 `omx_start --diagnostics`로 켭니다. 서비스 기본 실행 인자에는 `--diagnostics`가 없으므로 서비스에서 수집하려면 YAML을 사용합니다. 오래된 PID의 metrics를 현재 실행 결과로 해석하지 않습니다.

`logs/omxN/`에 순환 로그를, `state/manager/omxN/`에 `metrics.json`, `joint_errors.json`, `home_motion.json`, `recovery_wait.json` 등 최근 상태를 저장합니다. CPU 사용률의 100%는 보통 한 코어 기준이며 CPU 4스레드 설정이 모든 연산을 네 코어로 균등 분산시키지는 않습니다. 카메라 fps, 실제 제어 주기, ACT 청크 추론 시간은 별개입니다.

### Windows 노트북 브라우저에서 보기

서비스가 실행 중일 때 별도 Pi 터미널에서:

```bash
omx_view
```

노트북 브라우저에서 `http://<해당_Pi_IP>:8080/`을 엽니다. 기본 `view.allow_ip`는 `192.168.0.19`이며 노트북 주소가 바뀌면 [5절](#configuration)의 설정을 바꿉니다. `/all`은 여러 장비 보기 페이지입니다. 뷰어는 모터 명령을 보내지 않으며 서비스의 기존 카메라 영상을 공유합니다. 브라우저에서 선택한 저장 PNG는 촬영 시점의 기록이고 현재 영상과 다를 수 있습니다.

모델·서비스를 정지한 상태에서 카메라만 보려면:

```bash
omx_service stop
omx_view --camera-only
# Ctrl+C로 카메라 단독 뷰어를 종료한 뒤
omx_service start
```

기본 미리보기는 1fps이며 브라우저 요청 시 JPEG를 생성합니다. 실제 로봇 카메라/모델의 목표 fps를 1로 바꾸는 설정이 아닙니다.

<a id="recovery"></a>
## 7. 에러·복구 대기

| 상태 | 대응 |
|---|---|
| `RECOVERY_WAIT` | 로그의 원인과 실제 팔·물품 상태 확인. 조건을 바로잡은 뒤 실행 중인 감독기에 `omx_resume`으로 재점검 요청 |
| 중단 주문 기록 때문에 대기 | 실패 주문을 다시 실행하지 않음. 물품을 확인하고, 필요하면 실행기를 멈춘 뒤 `omx_recover`로 기록을 보관·정리 |
| `Outside approved Home approach area` | 임의 자세 복귀가 거절됨. 팔을 현장에서 적절한 자세로 배치하거나 검증된 경로 확인 |
| `Home movement timed out` | `home_motion.json`의 관절 오차와 `omx_motor_check`로 추종·토크·모드 확인. 홈을 덮어쓰는 방식으로 숨기지 않음 |
| `Torque OFF` | 기본은 현장 `omx_arm`. 전원 재인가 자동 준비는 [시작 토크 설정](#cold-start)의 조건에서만 선택 가능 |
| `Unverified startup torque/order state` | 시도 기록을 삭제하지 않음. 실제 팔·물품 확인 뒤 `omx_resume` 또는 실행기 정지 후 `omx_recover` |
| `Camera exposure restoration pending` | 실행기·watch를 종료한 뒤 `omx_basket restore` 완료를 기다림. pending 파일을 삭제하지 않음 |
| `Another runtime/calibration session owns ... device.lock` | 같은 장치를 쓰는 서비스·전경·보정·단독 뷰어를 정상 종료. 잠금 파일 삭제로 우회하지 않음 |
| `bad unit file setting` 또는 서비스 경로 변경 | 현재 코드를 받은 뒤 `omx_service install` → `enable` → `start`. install이 unit 생성·daemon-reload 수행 |
| `Could not resolve host: github.com` | 네트워크·기본 경로·DNS 확인. 코드/모터 문제와 구분 |

```bash
omx_service logs
omx_status
cat "state/manager/$OMX_ROBOT_ID/home_motion.json"
cat "state/manager/$OMX_ROBOT_ID/joint_errors.json"
cat "state/manager/$OMX_ROBOT_ID/recovery_wait.json"
```

`omx_resume`은 살아 있는 감독기의 재점검 요청이며, `omx_recover`는 실행기를 정지하고 실제 물품을 확인한 뒤 쓰는 기록 정리입니다. 둘 다 실패한 주문을 재전송하지 않습니다. 사용자 stop, 통신 장애, 추종 실패, 전원 차단은 같은 상황이 아니므로 서비스 재시작만으로 실제 적재 상태가 복구됐다고 판단하지 않습니다.

<a id="basket"></a>
## 8. 선택 기능: 바구니 검사

**OMX3·4 검사 OFF 운영에는 이 절의 ROI·노출·빈 기준 등록이 필요하지 않습니다.** 모델용 카메라는 계속 필요합니다. OFF에서는 검사 결과를 생성하지 않으므로 `omx_basket status`의 UNKNOWN/최신 검사 없음 표시만으로 구동 실패라고 판단하지 않습니다.

| 명령 | 동작 |
|---|---|
| `omx_basket off` / `omx_basket on` | 현장 YAML에 검사 OFF/ON 저장. 다음 실행부터 적용 |
| `omx_basket status` | 실행 컨트롤러의 최신 검사 상태 조회. 카메라를 따로 열지 않음 |
| `omx_basket snapshot` | 홈에서 상단 원본+좌표 격자 PNG 저장 |
| `omx_basket roi '[160,0,270,195]'` | ROI 예시 `[x,y,w,h]` 저장. 실제 사진으로 장비별 설정 |
| `omx_basket polygon '[[200,80],[350,80],[350,180],[200,180]]'` | 고정 ROI 안의 다각형 예시. 실제 ROI 내부의 점만 사용 |
| `omx_basket polygon null` | 고정 다각형 해제 |
| `omx_basket align on` / `align partial` / `align off` | 전체 정렬 / 부분 시야 정렬 / 고정 ROI 방식 선택 |
| `omx_basket pose` | 추적한 바구니·검사 영역 PNG 저장 |
| `omx_basket confirm-view` | 내부 전체와 정차 오차를 직접 확인한 경우 전체 시야 확인 저장 |
| `omx_basket confirm-visible` | 부분 시야에서 관측 범위를 직접 확인한 경우 확인 저장 |
| `omx_basket exposure` | 카메라 노출·게인·자동 설정 조회 |
| `omx_basket low 83` | 검사 전용 저노출 시험값 예시 저장. 카메라마다 직접 확인 필요 |
| `omx_basket low off` | 저노출 비교를 끄고 고정 RGB ROI 방식으로 변경. 바구니 검사 전체 OFF와 다름 |
| `omx_basket low-snapshot` | 검사 저노출 PNG를 저장하고 원래 설정·새 프레임 복원 |
| `omx_basket capture` | **현재 바구니가 비었음을 직접 확인한 경우에만** 빈 기준 저장 |
| `omx_basket check` | 연결·준비 후 단일 검사 |
| `omx_basket watch` / `watch --json` | 카메라 연결을 유지하는 간단/상세 판정 |
| `omx_basket diagnose` | 기존 기준·현재 영상·차이를 `basket_compare.png`에 저장. 기준을 바꾸지 않음 |
| `omx_basket restore` | 원래 카메라 설정 복원. 물리 작업을 재실행하지 않음 |
| `omx_basket collect` | 운영자가 빈/중앙 물건/가장자리 물건/다시 빈 상태를 확인하며 증거 ZIP 수집 |

on/off 설정 변경 외에 카메라·모터를 단독 관측하는 검사 명령은 서비스·전경 실행을 먼저 멈추고 사용합니다. 같은 카메라를 동시에 두 프로세스가 열지 않습니다. 기준 등록은 매 부팅·매 주문마다 실행하지 않습니다. 물건을 넣은 상태에서 capture하면 잘못된 빈 기준이 됩니다.

고정 `roi_polygon`이 남아 있으면 `omx_basket polygon null`로 해제한 뒤 정렬 방식을 켭니다. 검사 ON은 빈 기준과 시야·노출 등 별도 보정이 필요합니다. `omx_workflow`와 빈 상태·물건 중앙·벽 쪽·다시 빈 상태 시험을 모두 확인합니다. 부분 시야의 `visible_only` 결과는 화면 밖 물건까지 비었음을 증명하지 않습니다. ON 상태의 인자 없는 `omx_unload`는 제한 횟수·시간 안에서 관측 영역이 빌 때까지 반복하며, `omx_unload N`은 예상 수량과 빈 상태를 대조하므로 단순 N회 실행과 다릅니다.

증거 ZIP은 최신 `state/diagnostics/omxN_evidence.zip`에 저장됩니다. Windows PowerShell에서 내려받는 예시:

```powershell
scp omx03@192.168.0.153:/home/omx03/Logistics_OMX/state/diagnostics/omx3_evidence.zip "$env:USERPROFILE\Downloads\omx3_evidence.zip"
scp omx4@192.168.0.154:/home/omx4/Logistics_OMX/state/diagnostics/omx4_evidence.zip "$env:USERPROFILE\Downloads\omx4_evidence.zip"
```

계정·주소는 실제 장비에 맞춥니다. 세부 영상 검사 설명과 과거 변경 기록은 [OMX_사용설명서.md](OMX_사용설명서.md)를 참고합니다.

<a id="cli"></a>
## 9. 단축 명령 없이 사용하는 CLI 전체 목록

단축 함수가 없는 셸에서는 저장소 루트에서 아래 형태로 호출합니다. 먼저 장비 번호를 정확히 지정합니다.

```bash
cd ~/Logistics_OMX
OMX_ID=omx3
OMX_CFG="$PWD/state/config/$OMX_ID.yaml"
./omx_control --config "$OMX_CFG" --help
./omx_control --config "$OMX_CFG" validate
./omx_control --config "$OMX_CFG" plan
./omx_control --config "$OMX_CFG" simulate
```

`simulate`는 설정과 가상 사이클 시험이며 실제 카메라·모터·추론 성능 시험이 아닙니다. Python 선택은 `OMX_PYTHON`, 다음 `OMX_PYTHON_ENV_PATH/bin/python`, 기본 `~/venv/.venv/bin/python` 순서를 사용하며 실행 파일이 없으면 python3로 대체됩니다.

아래 표의 인자는 `./omx_control --config "$OMX_CFG"` 뒤에 붙입니다.

| CLI 명령·추가 인자 | 의미 / 대응 단축 명령 |
|---|---|
| `validate`, `check`, `plan`, `simulate` | YAML 검사 / 파일·홈 검사 / 실행 계획 / 가상 사이클 |
| `menu` | `omx_calibrate` |
| `run --confirm-motion --important-only --diagnostics` | `omx_start --diagnostics`의 공통 관리 실행 |
| `observe`, `motor-check --samples 20` | `omx_observe`, `omx_motor_check` |
| `capture` | `omx_capture`: 현재 홈 저장 |
| `arm --confirm-motion`, `home --confirm-motion`, `release --confirm-motion` | 토크 ON / 홈 이동 / 토크 OFF |
| `set --key KEY --value VALUE` | 허용된 YAML 필드 저장 |
| `setup-workflow`, `tune`, `workflow-check` | 관리 설정 추가 / 기본 최적화 설정 / 보정 유효성 확인 |
| `cycle-calibrate --item E --confirm-motion` | `omx_cycle_calibrate E` |
| `stage-capture --item E --name pick` | 이전 수동 단계 저장 |
| `stage-clear --item E` | 해당 품목의 수동 단계 제거 |
| `route-capture --name fold` | 복귀 경유점 저장 |
| `route-test --name fold --round-trip --confirm-motion` | 경로 왕복 시험·승인 |
| `return-home --confirm-motion` | 제한 복귀 |
| `order --item E --quantity 2` | OMX3 MQTT 2회 주문. 장비에 맞는 품목 사용 |
| `order --until-empty` | 검사 ON인 OMX3·4에서만 빈 상태까지 요청 |
| `logs --follow --lines 50`, `monitor --samples 1 --json` | 로그 / 상태·성능 조회 |
| `resume --confirm-motion`, `acknowledge-recovery --confirm-motion` | `omx_resume`, `omx_recover` |
| `service --action install` | 서비스 파일 설치만 수행 |
| `service --action start --confirm-motion`, `service --action enable --confirm-motion` | 서비스 시작 / 자동 실행 등록 |
| `service --action stop`, `service --action status`, `service --action logs --follow` | 서비스 정지 / 상태 / 로그 |
| `service --action disable`, `service --action uninstall` | 자동 시작 해제 / 서비스 제거 |
| `basket-capture --confirm-empty` | 빈 기준 저장 |
| `basket-check`, `basket-watch --json`, `basket-status`, `basket-snapshot` | 검사 / 연속 검사 / 최신 판정 / 원본 PNG |
| `basket-exposure`, `basket-low --value 83`, `basket-low-snapshot` | 노출 조회 / 시험 노출 설정 / 저노출 PNG |
| `basket-restore-exposure`, `basket-diagnose`, `basket-pose`, `basket-collect` | 원래 설정 복원 / 차이 PNG / 추적 PNG / 증거 수집 |

공통 선택 인자: `--expected-device omx3`(장비 일치 확인), `--important-only`, `--diagnostics`, `--follow`, `--lines N`, `--interval 초`, `--samples N`, `--json`. 실제 적용 가능한 조합은 위 명령 설명과 `--help`를 따릅니다. 모터 이동 명령의 단축 함수는 필요한 확인 플래그를 이미 포함합니다.

카메라 뷰어는 별도 실행기입니다:

```bash
./omx_view --config "$OMX_CFG"
# 모델·서비스를 멈춘 상태에서만:
./omx_view --config "$OMX_CFG" --camera-only
```

<a id="development"></a>
## 10. 개발 검증·dev/main·서브모듈

### 하드웨어 없는 검증

장비별 동작 환경과 분리한 개발 PC에서, pytest·PyYAML·NumPy·OpenCV가 준비된 Python으로 실행합니다. 새 환경이면 별도 venv에 이 검사 의존성을 준비합니다. 기본 단위 검사는 모델 가중치나 실제 로봇을 필요로 하지 않으며 테스트용 localhost 소켓 권한은 필요할 수 있습니다.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest tests -q -p no:cacheprovider
python scripts/verify_omx.py --runs 5
bash scripts/check_omx_release.sh
```

서비스 검사는 systemd-analyze가 설치된 Linux에서 실제 unit 문법을 검증하며 로봇 서비스를 시작하지 않습니다. 가상 시험 통과는 Pi의 추론 속도·실제 집기·부팅 재시작 성공을 보증하지 않습니다.

수집 ZIP을 개발 PC의 `evidence/omx3`, `evidence/omx4`에 푼 뒤 영상 재검증:

```bash
python scripts/replay_basket_evidence.py --root evidence --report reports/field_replay.json
```

이 명령은 저장된 정답·실제 프레임과 명시적 정지 장면 모의 입력을 구분하며 새 현장 촬영을 대신하지 않습니다.

### 브랜치와 커밋

일반 배포는 main, 개발 변경은 dev에 커밋·검증 후 반영합니다. 커밋 메시지는 **영어: 한글 설명** 형식을 사용합니다. 아래 파일명은 실제 변경한 파일로 바꿉니다.

```bash
git switch dev
git pull --ff-only origin dev
git add README.md COMMANDS.md
git commit -m "Docs: OMX 사용법과 명령어 문서 정리"
git push origin dev
# dev 검증과 반영 범위를 확인한 뒤
git switch main
git pull --ff-only origin main
git merge --no-ff dev -m "Merge: 검증한 dev 변경을 main에 반영"
git push origin main
```

main에 별도 변경이 있으면 먼저 그 내용을 보존·통합하고 충돌을 검토합니다. 강제 push나 원격 기존 커밋 재작성은 일반 배포 절차가 아닙니다. 실행 중인 Pi에서 개발용 브랜치 병합을 진행하지 않습니다.

상위 `AX_Logistics`에서 이 저장소를 서브모듈로 쓸 때 **OMX 파일 변경은 OMX 저장소 안에서 commit/push**합니다. 상위 저장소는 OMX의 특정 커밋을 가리키므로 새 버전을 채택하려면 상위에서도 그 포인터를 별도 commit/push해야 합니다. 독립 clone한 Pi에는 상위 포인터 커밋이 없습니다. [README](README.md)


<a id="direct-home"></a>
### 현장 선택: 홈 거리 제한 없이 현재 자세에서 직접 복귀

충돌 가능성이 없다고 현장에서 판단한 OMX1~4에서 `recovery.direct_home: true`를 선택합니다. 관절 위치만으로 바구니나 장애물을 정확히 인식한다고 가정하지 않습니다. 임의의 완전 신전 중간 자세 없이 현재 관절에서 저장 홈까지 목표를 조금씩 이동합니다. 충돌 계획은 하지 않습니다. 속도·1회 목표 이동량·추종 오차·피드백 지연·모터 건강·보정 범위·도착 정지 판정은 유지합니다. 시작 홈 오차 2.0과 일반 접근 거리 때문에 시작을 막지 않으며 이동 거리에 맞춰 시간 예산만 최대 300초까지 계산합니다. 현장 YAML의 기존 홈·속도·카운팅 값은 변경하지 않습니다.

각 Pi에서 팔·공간과 물품을 확인한 뒤 적용합니다. 시작 준비와 수동 home은 그리퍼도 등록 홈 값으로 움직이므로 물품을 먼저 내려놓습니다. 동작 중 오류 복귀는 그리퍼를 유지합니다.

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service stop
git pull --ff-only
./omx_control --config "$OMX_CONFIG_PATH" set --key recovery.direct_home --value true
./omx_control --config "$OMX_CONFIG_PATH" set --key recovery.startup_arm_at_home --value true
omx_service start
omx_service logs --follow
```

수동 복귀는 서비스를 멈추고 `omx_arm` 성공 뒤 `omx_home`으로 확인합니다. 토크 ON은 현재 위치 목표 저장 뒤 수행하며 전체 OFF·정지·모드·보정·건강 검사는 유지합니다. 일부 관절만 OFF, 통신 불량, 모터 자체 오류, 추종 실패, 이전 미확인 토크 활성화 기록을 무시하지 않습니다. 이 선택은 충돌 없는 복귀나 모든 기계 고장 해결을 보증하지 않습니다.

관리 실행기 상태·주요 복구/홈 오류·제어 주기 경고의 운영자 출력과 중요 로그는 한글로 표시합니다. 프로토콜 코드·관절 키·원래 예외·상세 로그는 진단을 위해 유지하며 외부 라이브러리의 모든 출력을 번역하지는 않습니다. 가상 시험 후 실제 Pi에서 물품 없이 저속 복귀부터 확인합니다.


### 특정 관절만 홈 도착 허용 오차 조정

`home.arrival_tolerances`는 개별 관절의 도착 판정만 덮어씁니다. 나머지 관절은 `home.arrival_tolerance`(기본 0.8)를 사용합니다. 정규화 단위이며 개별 값은 최대 3입니다. 토크 ON 순간 이동 감시·기존 카운팅·추종 오차·등록 경로의 엄격한 웨이포인트 판정은 바꾸지 않습니다.

OMX2 현장 기록은 손목 굽힘이 목표에서 약 1.71 떨어진 위치에 정지해 0.8 기준으로 시간초과했습니다. 해당 관절만 2.0으로 시험하려면 **OMX2에서만** 실행합니다. 실제 도착 오차를 허용하는 것이며 모터 정확도를 높이는 설정은 아닙니다.

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service stop
git pull --ff-only
./omx_control --config "$OMX_CONFIG_PATH" set --key home.arrival_tolerances.wrist_flex --value 2.0
omx_service start
omx_service logs --follow
```

홈·모델·모터 보정·PID는 재등록하거나 변경하지 않습니다. 손목의 계속되는 흔들림·오차 증가·하드웨어 오류는 별도 확인합니다. 원래 판정으로 되돌리려면 같은 개별 값을 0.8로 저장하고 재시작합니다.


### 선택한 로봇 한 대의 상단·손목만 받기

최신 코드로 갱신한 각 Pi에서 로봇 서비스를 다시 시작한 뒤 별도 터미널에서 `omx_view`를 실행합니다. 기존 뷰어가 켜져 있으면 그 뷰어 터미널에서 Ctrl+C로 종료하고 다시 실행합니다. 서비스 로그 보기 종료와 서비스 종료는 다릅니다.

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service stop
git pull --ff-only
omx_service start
# 별도 터미널에서 웹 뷰어 실행
omx_view
```

Windows 브라우저에서 `http://192.168.0.151:8080/all`을 엽니다. OMX1·2·3·4 개별 탭만 제공합니다. 한 번에 선택한 한 로봇의 상단·손목만 받습니다. 선택한 로봇별 상단·손목 영상을 함께 표시하며 기본 각 1fps입니다. OMX1·2의 상단은 front, OMX3·4는 top, 손목은 wrist입니다. 주소 설정에서 각 Pi IP를 수정할 수 있습니다. 탭을 바꾸면 이전 iframe을 제거해 선택하지 않은 로봇의 새 영상 요청을 중지합니다. 기존 PNG 스냅샷은 각 로봇 화면의 접힌 메뉴에서 확인합니다.

실행 중 모델의 기존 카메라 버퍼를 카메라별 공유 채널로 사용합니다. 웹 요청이 없을 때 JPEG를 만들지 않으며 모델 입력·해상도·노출·모터 명령을 바꾸지 않습니다. 한 카메라가 없거나 오래된 영상이면 해당 칸만 대기 표시합니다. 새 손목 공유 채널을 만들려면 업데이트 뒤 로봇 서비스 재시작이 필요합니다.

모델 없이 두 카메라만 보고 싶으면 서비스를 멈추고 `omx_view --camera-only`를 실행합니다. 이때 상단·손목 두 장치를 단독 점유하므로 모델과 동시에 실행하지 않습니다. 종료 시 두 카메라와 미리보기 작업을 정리합니다. 뷰어 주소/IP 제한·기존 스냅샷 경로는 유지합니다.

기존 `/live.jpg`, `/status.json`은 기본 카메라를 계속 제공하며 `/live.jpg?camera=wrist`, `/live.jpg?camera=top` 또는 front로 개별 조회할 수 있습니다. `/status.json?camera=all`은 등록 카메라별 상태와 영상 요청을 제공합니다. 모터·카메라 설정을 바꾸는 웹 API는 없습니다.

처음 접속하는 `/`와 `/all` 모두 로봇 선택 탭입니다. 선택한 로봇만 `/robot` 화면을 열며 다른 로봇 영상은 요청하지 않습니다. 탭 전환 시 이전 화면 제거와 진행 중 영상 요청 취소를 수행합니다. 2대 묶음 보기는 제거했습니다.


### PC IP 제한 없이 보기 / 서비스 정지 상태에서 영상 보기

기본 `view.allow_ip`는 192.168.0.19 한 PC만 허용합니다. .16 PC 등 주소가 달라도 접속하려면 실행 중 뷰어를 Ctrl+C로 종료하고, 서비스를 정지한 상태에서 다음 값을 저장합니다. `any`는 Pi에 네트워크로 도달 가능한 모든 IP를 허용하며 읽기 전용 영상·스냅샷 API만 제공합니다. 로봇/Pi 주소 자체나 SSH 연결을 복구하는 설정은 아닙니다.

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service stop
git pull --ff-only
./omx_control --config "$OMX_CONFIG_PATH" set --key view.allow_ip --value any
# 모델 없이 상단·손목 카메라만 보고 싶을 때
omx_view --camera-only
```

모델과 같이 보려면 카메라 단독 뷰어를 종료한 뒤 `omx_service start`하고 `omx_view`를 실행합니다. 일반 `omx_view`는 카메라를 직접 열지 않으므로 서비스까지 멈추면 영상이 없습니다. 다시 특정 PC로 제한하려면 뷰어·서비스 정지 후 `view.allow_ip`에 해당 IPv4(예: 192.168.0.16)를 저장하고 뷰어를 재실행합니다.


<a id="pallet-stock"></a>
## OMX1·2 고정 팔레트 물건 존재 검사

외부 품목 기준 OMX1 왼쪽=A / 오른쪽=B, OMX2 왼쪽=C / 오른쪽=D입니다. OMX1의 A가 B 모델을 사용하는 기존 실행 매핑은 바꾸지 않습니다. 각 팔레트에 물건이 하나라도 있는지만 검사하며 수량·종류·실제 집기 성공은 판단하지 않습니다. FMS 수신부·화면·주문 정책은 수정하지 않습니다.

기본은 꺼져 있고 ROI·빈 기준은 임의 등록하지 않습니다. 주기 기본 5초, 같은 결과 3회 연속 확인 뒤 확정하므로 약 10~15초가 필요합니다. 홈 대기·정지 중에만 기존 front RGB 버퍼를 읽습니다. 새 카메라·시리얼·모델 추론·노출 조작 없이 최대 변 160픽셀의 ROI만 백그라운드 비교합니다. 작업·이동·관측 지연·과노출·조명 차이·미등록·기준 불일치·불명확한 변화는 UNKNOWN입니다.

### 1. 업데이트 및 검사 준비 (OMX1·2에서만)

```bash
cd ~/Logistics_OMX
omx_service stop
git pull --ff-only
source ~/.bashrc
omx_stock setup
omx_stock on
omx_service start
```

홈·모델 준비와 MQTT 연결이 끝날 때까지 서비스 로그를 확인합니다. 첫 시작에서 ROI·기준 미등록으로 판정 불가가 나오는 것은 정상입니다. 주문이 없는 홈 상태에서 별도 터미널로 실행합니다:

```bash
omx_stock snapshot
omx_stock status
```

사진은 `state/manager/omxN/omxN_pallet_view.png`에 저장됩니다. 최신 `omx_view`의 스냅샷 메뉴에서 ‘팔레트 좌표·영역 사진’을 누르거나 `/saved/stock.png`로 봅니다. 사진에 격자와 좌표가 표시됩니다. 일반 웹 뷰어는 별도 터미널에서 실행하고, 업데이트 전에 실행 중이던 뷰어는 다시 켭니다. 이 명령은 모델을 새로 돌리거나 팔을 이동시키지 않으며, 실행 중인 로봇의 카메라 소유자에게 촬영을 요청합니다.

### 2. 실제 사진으로 팔레트 ROI 지정

서비스를 멈춘 뒤 `omx_stock roi 품목 '[x,y,너비,높이]'`로 각 팔레트 영역을 지정합니다. 좌표는 실제 front 영상 기준입니다. 사진 예시에서 임의 추측한 좌표를 기본값으로 넣지 않았습니다. OMX1은 A/B, OMX2는 C/D 두 영역을 각각 지정합니다. 팔레트 테두리·케이블·옆 작업 영역을 과도하게 포함하지 않도록 실제 검사할 내부 영역을 선택합니다. 저장 뒤 서비스를 다시 시작해야 적용됩니다.

설정은 현장 YAML `pallet_stock`에 저장하며 모든 값에 한글 주석이 붙습니다. `interval_s`, `stable_samples`, `pixel_threshold`, `weak_pixel_threshold`, `min_component_pixels`, `empty_ratio`, `max_frame_age_s`, `max_color_offset`를 조정할 수 있습니다. 예: 서비스 정지 후 `./omx_control --config "$OMX_CONFIG_PATH" set --key pallet_stock.interval_s --value 10`. 실물 결과 없이 임계값을 낮추어 UNKNOWN을 숨기지 않습니다.

### 3. 각 팔레트를 완전히 비운 상태에서 기준 저장

서비스가 READY이고 팔·손이 검사 영역을 가리지 않을 때 실행합니다. `capture`는 사용자가 해당 팔레트가 비었다고 확인하는 명령입니다. 물건이 있는 상태를 빈 기준으로 저장하면 잘못된 판정이 나올 수 있습니다.

```bash
# OMX1: 왼쪽 A와 오른쪽 B를 각각 완전히 비운 상태
omx_stock capture A
omx_stock capture B
# OMX2에서는 위 두 명령 대신
# omx_stock capture C
# omx_stock capture D
omx_stock status
omx_stock status --json
```

새 영상 3장으로 정지·해상도를 확인하고 `state/manager/omxN/pallet_refs/품목.npz`에 저장합니다. 카메라·ROI·홈 등록 연결 정보가 달라지면 기존 기준을 사용하지 않습니다. 자동으로 빈 기준을 갱신하지 않습니다. 중복/오래된 프레임으로 연속 확정을 쌓지 않습니다.

### 4. 반드시 확인할 실물 시험

팔레트별로 빈 상태 → 가장 작고 밝은 물건 1개 중앙 → 모서리 근처 → 여러 물건 → 다시 빈 상태를 시험합니다. 변화 직후에는 UNKNOWN이 나오고 연속 확인 뒤 확정됩니다. 손으로 가리거나 팔이 이동·작업 중일 때 EMPTY를 확정하지 않는지도 확인합니다. 가상 영상 시험은 실제 조명·흰 물건·가림의 인식률을 보장하지 않습니다. 카메라 위치가 고정이어도 실제 프레임과 현장 시험으로 ROI·기준을 검증해야 합니다.

### MQTT (OMX 송신만 구현, FMS는 추후 연결)

활성화된 OMX1·2는 기존 MQTT 프로세스에서 `omx1/stock`, `omx2/stock`으로 약 2초마다 최신 관측 상태를 전송합니다. 새 영상 검사는 설정 주기마다 실행됩니다. QoS 0, retain=false이며 기존 command/ack/progress/result·주문 inventory.json과 독립입니다. 모델 PID·세션·주문 시작 시각·관측 유효기간을 확인하여 이전 실행·이전 주문·오래된 결과는 UNKNOWN으로 전송합니다. 새 구독자는 다음 주기 메시지를 기다립니다.

```json
{"robot_id":"omx1","stock":{"A":"PRESENT","B":"EMPTY"},"at":1793500000,"observed_at":1793499999,"valid_for_s":12.5,"exact_item_count":null}
```

상태는 PRESENT(있음), EMPTY(비었음), UNKNOWN(판정 불가)입니다. `at`는 송신 시각, `observed_at`는 유효한 관측 결과 시각이며, UNKNOWN 사유와 품목별 비교 수치도 포함됩니다. FMS에서는 observed_at·valid_for_s와 로봇 연결 상태를 함께 확인하도록 추후 구현해야 합니다. 재고 메시지를 실제 집기 성공이나 정확한 수량으로 해석하지 않습니다.

검사를 끄려면 서비스를 멈추고 `omx_stock off` 후 재시작합니다. 종료 시 검사 스레드를 먼저 정리하고 카메라를 닫습니다. 상태 조회는 파일만 읽고 모델·카메라·모터를 열지 않습니다.


<a id="omx3-model4"></a>
## OMX3: OMX4 모델 + RealSense 상단 RGB 임시 사용

OMX3 전용 모델이 준비될 때까지 OMX4의 `warehouse_item_f_v3_5_act_300k/checkpoints/230000/pretrained_model`을 `omx4_temporary` 프로필로 실행합니다. 외부 장비/주문은 **omx3 / E**를 유지하며 MQTT·서비스·모터 포트·모터 보정 파일을 OMX4 것으로 복사하지 않습니다. 기존 E 모델은 `original` 프로필에 보존합니다. 상단 `top`은 RealSense RGB 640×480/30fps, 손목 `wrist`는 기존 USB 카메라를 사용합니다. 깊이는 사용하지 않습니다. 실제 설치 각도·관절 조립·시야가 학습 환경과 맞는지는 현장 동작으로 확인해야 합니다.

아래는 **OMX3에서만**, 서비스를 멈추고 실행합니다. 전환 명령은 모터를 쓰거나 영상 스트림·모델을 실행하지 않습니다. RealSense가 한 대면 시리얼을 자동 검색합니다. 여러 대면 `--serial 실제시리얼`을 추가합니다.

```bash
cd ~/Logistics_OMX
source ~/.bashrc
omx_service stop
git pull --ff-only
"$OMX_PYTHON" -m omx_manager.model_compat --config "$OMX_CONFIG_PATH" --apply
```

F 모델 폴더와 processor가 OMX3에도 있어야 합니다. 파일이 없으면 설정을 바꾸기 전에 실패합니다. OMX4의 동일한 pretrained_model 전체를 OMX3에 복사한 뒤 다시 실행합니다. 다른 경로에 있다면 `--model-path /실제/pretrained_model`을 추가합니다. 이 도구는 모델을 다운로드하거나 학습하지 않습니다. `--apply`를 빼면 파일 확인·변경 계획만 출력합니다.

변경 전 YAML 전체는 `state/config/backups/`에 저장합니다. 기존 홈 **좌표는 보존**하지만 모델·카메라 연결 검증은 무효화합니다. 이전 사이클·단계·복귀 경로·바구니 기준을 새 모델에서 검증된 것으로 사용하지 않습니다. 바구니 검사·저노출·기존 바구니 추적(registration_enabled)은 함께 OFF로 전환하므로 노출·빈 기준을 다시 맞출 필요가 없습니다. 미완료 주문/복구 기록은 임의 삭제하지 않습니다. 카메라 노출 복원 대기가 남아 있으면 이전 카메라 설정에서 `omx_basket restore`를 완료한 뒤 전환합니다.

**팔을 새 모델의 초기 자세에 두고 정지한 상태를 확인한 후** 아래 순서로 진행합니다. `omx_capture`는 현재 자세를 저장하므로 뻗은 자세에서 실행하면 안 됩니다. 홈으로 자동 이동시키는 명령이 아닙니다.

```bash
omx_capture
omx_arm
omx_check
omx_cycle_calibrate E
omx_workflow
omx_service start
omx_service logs --follow
```

`omx_cycle_calibrate E`에서는 임시 F 모델이 실제로 움직입니다. 정상 집기·놓기·복귀 3회를 사람이 확인해 저장합니다. OMX4의 사이클 보정을 OMX3에 복사하지 않습니다. READY 후 별도 터미널에서 `omx_unload 1`로 한 사이클을 먼저 시험합니다. 전체를 정상 수행하는지 확인한 뒤 `omx_unload 2`로 두 사이클을 확인합니다. 숫자는 검증된 동작 횟수이며 실제 물품 성공 개수와 다를 수 있습니다.

새 OMX3 모델로 돌아갈 때는 기존 모델 경로·입력 카메라 요구사항을 먼저 확인하고 활성 프로필을 변경한 뒤 홈·사이클을 다시 확인합니다. 예전 C920 설정을 포함한 YAML 백업 전체를 RealSense 환경에 그대로 복원하지 않습니다. 같은 모델·시리얼로 전환 명령을 반복하면 새로 등록한 홈·사이클을 다시 초기화하지 않습니다.


### 영상 파일의 로봇 번호

웹 뷰어의 ‘이 카메라 영상 저장’은 `omx1_front.jpg`, `omx3_top.jpg`, `omx4_wrist.jpg`처럼 로봇 번호·카메라 이름으로 다운로드합니다. 저장된 스냅샷도 `omx3_basket_low.png`, `omx2_pallet_view.png`처럼 내려받습니다. 다운로드 이미지 아래에 OMX 번호·카메라 이름 식별 띠가 추가되며 모델 입력·기존 공유 RGB 버퍼는 바꾸지 않습니다. 식별 띠는 원본 아래에 붙으므로 원본 ROI 좌표는 유지됩니다. 일회성 다운로드 때만 변환하며 실시간 조회에는 추가 변환하지 않습니다. `omx_stock snapshot`의 현장 파일명과 응답에도 로봇 번호가 표시됩니다. 이전 이름의 스냅샷도 계속 볼 수 있습니다. 업데이트 후 실행 중 웹 뷰어를 종료·재실행하고 브라우저를 새로고침해야 적용됩니다.

### 새 OMX3 E 모델 경로로 교체

실제 실행 설정은 `$OMX_CONFIG_PATH`(보통 `state/config/omx3.yaml`)입니다. `params/omx3.yaml`은 새 장비용 템플릿이므로 기존 Pi의 이 파일만 수정하면 적용되지 않습니다. 서비스를 멈춘 뒤 실제 설정의 `models.active_profile`이 가리키는 프로필에서 `items.E.path`를 새 `pretrained_model` 폴더로, `items.E.task`를 학습한 작업 문장으로 수정합니다. 현재 활성 임시 프로필은 `omx4_temporary`입니다. 새 프로필을 만들어 `active_profile`로 선택해도 됩니다. 외부 주문 키 `E`는 유지합니다.

새 모델은 현 실행기 기준 FP32 LeRobot ACT 6축이고 카메라 키 `top`·`wrist`, 입력 크기가 현장 설정과 맞아야 합니다. 가중치 외 config·pre/postprocessor·processor state도 폴더에 필요합니다. 교체 후에는 모델 해시가 달라지므로 정상 초기 자세 확인 → `omx_capture` → `omx_arm` → `omx_check` → `omx_cycle_calibrate E` → `omx_workflow` 후 서비스를 시작합니다. 이전 사이클 보정 해시를 새 모델에 억지로 붙이지 않습니다. F 모델 재적용 도구 `model_compat --apply`를 새 E 모델 설정 후 다시 실행하면 F 모델로 돌아가므로 사용하지 않습니다.


### OMX1·2 현재 상단 사진 기준 ROI 시작값 (640×480)

2026-11-01 전달받은 현장 사진 기준이며 왼쪽 A/C, 오른쪽 B/D입니다. 아래는 `pallet_stock` 검사 영역만 바꾸며 모델 영상·홈·카운팅 설정은 바꾸지 않습니다. 템플릿을 덮어쓰지 않고 실제 현장 YAML에 저장합니다. 카메라 위치·해상도가 바뀌면 새 사진으로 다시 지정합니다.

```bash
# OMX1에서, 서비스 정지 상태
omx_stock roi A '[28,280,128,116]'
omx_stock roi B '[472,272,132,124]'
# OMX2에서는 위 두 줄 대신
omx_stock roi C '[28,258,130,114]'
omx_stock roi D '[482,248,132,120]'
```

서비스 재시작 후 READY를 확인하고 각 팔레트를 완전히 비운 상태에서 OMX1은 `omx_stock capture A`, `omx_stock capture B`, OMX2는 `omx_stock capture C`, `omx_stock capture D`로 빈 기준을 등록합니다. 이번에 전달한 물건이 올라간 사진을 빈 기준으로 쓰지 않습니다. 10~15초 뒤 `omx_stock status`로 빈 상태를 확인하고, 물건 한 개 넣기·다시 빼기를 시험합니다. `omx_stock snapshot`으로 ROI 표시를 함께 확인합니다.


### 팔레트 검사: 작은 색상 변화 보정

`pallet_stock.max_color_offset`은 기본 18(0~255 RGB 값), 설정 가능 범위 0~30입니다. 기존 현장 YAML에 이 키가 없으면 기본값을 사용합니다. 저장한 빈 기준 대비 채널별 중앙 색상 차이가 이 한도 이내이면 ROI 사본에서만 차이를 빼고, 보정 뒤 남은 국소 변화 연결 영역으로 존재 여부를 판단합니다. OMX1 현장 `[1,-2,-7]`처럼 기존 고정 6 기준에서 차단되던 작은 차이는 비교를 계속합니다. 큰 색상 변화·과노출은 UNKNOWN으로 유지하며 모델 입력이나 센서 노출을 바꾸지 않습니다.

`omx_stock status --json`에 `color_offset_limit`, `clipped_ratio`, `reference_clipped_ratio`, `clipped_limit`, `quality_flags`, `features_computed`를 표시해 색상 한도 차단과 실제 과노출을 구분합니다. 같은 결과 3회 연속 확인은 유지합니다. 이 변경은 기존 ROI·빈 기준 파일 형식을 바꾸지 않으므로 재등록하지 않고 서비스 정지 → Git 업데이트 → 서비스 시작으로 적용합니다. 물건 있는 상태와 모두 뺀 상태를 각각 확인해야 실제 현장 성능을 판단할 수 있습니다.


### OMX 프로세스 전체 종료: omx_kill

```bash
omx_kill --list       # 종료 없이 대상 PID만 확인
omx_kill              # 서비스 자동 재시작 차단 → 정상 종료 요청 → 5초 후 남으면 강제 종료
omx_kill --timeout 2  # 정상 종료 대기 시간을 2초로 지정
```

현재 Pi의 현재 사용자·현재 저장소에 속한 OMX 실행기, MQTT, 보정, 뷰어, 모니터 및 그 자식 프로세스를 정리합니다. 사용자 OMX systemd 서비스부터 명시적으로 stop해서 Restart=always에 의한 재실행을 막습니다. 해당 서비스는 다음 부팅 자동 시작 설정까지 해제하지는 않습니다. SSH·로그인 셸·다른 Python 프로그램·다른 저장소·다른 사용자 프로세스는 대상으로 삼지 않습니다. 로컬 Pi 명령이므로 다른 OMX Pi까지 종료하지 않습니다.

종료가 걸린 터미널에서는 새 SSH 터미널을 열어 실행합니다. 모터 API나 홈 이동·토크 OFF를 호출하지 않습니다. 강제 종료 시 OS가 파일·USB·포트 핸들을 회수하지만 라이브러리의 정상 종료와 카메라 노출 복원까지 보장하지는 않습니다. 미완료 작업·노출 복원 기록과 lock 파일을 삭제하지 않습니다. 파일 잠금은 프로세스 종료로 해제됩니다. 커널 I/O 대기 등으로 PID가 남으면 성공으로 표시하지 않고 해당 PID를 알려줍니다. 정상 재실행은 `omx_service start` 또는 카메라만 볼 때 `omx_view --camera-only`입니다.


## OMX1·2 팔레트 정밀 비교 모드

A/B/C/D 고정 팔레트의 조명 변화·미세 이동·가는 격자 차이를 검사 ROI 사본에서만 보정합니다.
`COLOR_OFFSET_LIMIT`가 떠도 무조건 한도를 높이지 않습니다. 여러 위치의 빈 격자가 형태를 유지하고 공통 색상 변화를 보일 때만 기본 18을 넘어 최대 40까지 보정합니다.
근거가 부족하거나 과노출이면 UNKNOWN을 유지합니다. 물건 영역을 새 빈 기준으로 자동 학습하지 않습니다.
흰 물건처럼 RGB 차이가 작아도 빈 팔레트 격자가 물건 크기 영역에서 사라지면 별도 존재 근거로 사용합니다. `grid_occlusion_pixels`로 이 근거를 확인할 수 있습니다.
ROI·기존 빈 기준·모델·노출·홈·카운팅·MQTT 형식은 그대로 유지합니다.

각 OMX1·2 Pi에서:

```bash
cd ~/Logistics_OMX
omx_service stop
git pull --ff-only origin dev
source ~/.bashrc
omx_stock improve
omx_service start
```

READY 이후 기본 설정에서는 10~15초 동안 관측을 쌓습니다.

```bash
omx_stock status
omx_stock status --json
```

`precision_enabled`는 현장 검증을 위해 기본 false입니다. `improve`는 해당 Pi의 A/B 또는 C/D **두 팔레트 모두**에 정밀 비교를 켭니다.
빈 기준 재등록은 필요하지 않습니다. 업데이트 전에 찍은 빈 기준은 계속 사용합니다.
서비스가 기존 오류의 자동 재시도 상한으로 대기한다면 팔·물건 상태를 확인하고 `omx_resume`으로 재점검합니다.

설정은 실제 `state/config/omxN.yaml`의 `pallet_stock`에 저장합니다.

| 키 | 기본값 | 의미 |
| --- | --- | --- |
| precision_enabled | false | 배경 격자 조명 보정·작은 이동 정렬·가는 차이 제거 |
| adaptive_color_limit | 40 | 여러 배경 격자가 일치할 때만 허용할 추가 RGB 보정 한도 |
| alignment_max_px | 3 | 최대 변 160으로 축소한 ROI에서 이동 탐색 범위 |
| reference_slack_px | 2 | 정렬 후 허용할 격자 오차 반경 |
| min_object_width_px | 3 | 가는 격자 차이 제거 폭. 1/3/5, 키우면 작은 물건을 놓칠 수 있음 |

물건 있음 → 완전히 비움 → 가장 작은 물건 한 개 → 모서리 → 다시 비움 순서로 A/B/C/D를 각각 확인하세요.
한 장면의 성공으로 전체 조명·가림에서의 정확도가 보장되지는 않습니다. `processing_ms`는 실제 해당 Pi의 품목별 처리 시간입니다.
영상 전체를 신경망으로 다시 검사하거나 카메라를 추가로 열지 않습니다. 검사 주기는 기존 설정을 따릅니다.

### 실제 기준·현재 영상 비교 저장

서비스가 READY이고 홈에서 대기 중일 때 실행합니다. 팔레트가 비었든 물건이 있든 진단할 수 있습니다. 기준을 덮어쓰지 않습니다.

```bash
# OMX1
omx_stock diagnose A
omx_stock diagnose B
# OMX2
omx_stock diagnose C
omx_stock diagnose D
```

`state/manager/omxN/omxN_pallet_A_diagnose.png` 같은 이름으로 빈 기준·현재 ROI·원시 차이를 저장합니다.
같은 접두사의 `_evidence.npz`와 `_diagnose.json`에는 실제 ROI와 설정·수치가 저장됩니다.
원시 차이 그림은 위치·조명 보정 전이므로 최종 물건 마스크와 다릅니다.
판정이 틀린 상태의 NPZ/JSON과 실제 물건 유무를 보내면 동일 영상을 재촬영 없이 재검증할 수 있습니다.
개발/분석용 재검사:

```bash
"$OMX_PYTHON" scripts/replay_pallet_evidence.py state/manager/omx1/omx1_pallet_A_evidence.npz --repeat 5
```

기존 방식으로 되돌리려면:

```bash
omx_service stop
omx_stock legacy
omx_service start
```
