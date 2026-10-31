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
