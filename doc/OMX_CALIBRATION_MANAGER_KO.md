# OMX1~4 팔로워 전용 설정·홈 등록·복구 도구

작성: 2026-10-29. 기준 브랜치: `feat/omx-calibration-manager` (기반 `92f2165`).

## 이번 구현 범위

공통 Python 코드와 `params/omx1.yaml`~`omx4.yaml`을 사용합니다. 리더암은 필요하지 않습니다.
`omx_control`은 YAML 검사, 관절 관측, 현재 자세 등록, 제한 홈 이동, 설정 저장/백업,
시뮬레이션, 실행 계획 출력과 실제 운용 감독을 제공합니다.

- 기존 모터 보정 JSON의 영점/방향/범위는 읽어서 검사하며 자동으로 쓰지 않습니다.
- `lerobot-calibrate`의 OMX 기본값 쓰기 경로도 차단했습니다. 이 도구는 물리적인 영점 재교정기가 아닙니다.
- 홈 미등록, 모델/보정/카메라 변경, 장치 모드 불일치, 범위 밖 엔코더값은 구동을 차단합니다.
- 홈 위치는 모델별 실행 프로필과 보정 파일 및 모델/processor 파일 해시에 연결됩니다.
- 위치 단위는 학습 때 사용한 정규화 값입니다. 각도로 해석하거나 `use_degrees`로 바꾸지 마세요.
- 실제 물체 이송 성공 검출이나 빈 바구니 인식은 구현한 것이 아닙니다. 카운트는 홈 복귀 사이클입니다.
- 양자화는 수행하지 않았습니다. INT8 후보를 YAML에 등록할 자리는 있지만 실행 시 명확히 거절합니다.

## 압축 소스 사용 시

배포 압축에는 모델 가중치·개인 보정 파일·venv·실행 중 상태를 넣지 않습니다.
새 점검 폴더에 먼저 풀고 실제 구형 런타임을 중지한 뒤 사용합니다.

```bash
mkdir -p ~/omx-calibration-preview
tar -xzf OMX_Calibration_Manager.tar.gz -C ~/omx-calibration-preview
cd ~/omx-calibration-preview/Logistics_OMX
# 기존 모델을 이동/삭제하지 않고 참조합니다. outputs가 없을 때만 수행합니다.
if [ ! -e lerobot_unified/outputs ] && [ ! -L lerobot_unified/outputs ]; then
  ln -s "$HOME/il_ws/src/lerobot/outputs" lerobot_unified/outputs
fi
./omx_control --config params/omx1.yaml menu
```

미리보기 폴더에서는 `./omx_control`을 사용합니다. 기존 `omx_start` 함수와 서비스 예시는 `~/Logistics_OMX`를 대상으로 하므로 정식 반영 후 사용합니다.
압축 안의 `MANAGER_BUILD.json`에는 소스 커밋·파일 해시·가상 검증 요약이 있습니다.

## 준비

저장소를 Pi의 `~/Logistics_OMX`에 적용합니다. 기존 모델, 가상환경, 캘리브레이션을 보존합니다.
`main`으로 되돌리는 것만으로 장비의 YAML/보정/모델 상태가 복원되는 것은 아닙니다.
소스만 갱신하고 실행 중인 구형 프로세스는 먼저 종료합니다. 이 도구가 다른 코드 버전의 포트 점유까지 강제로 해제하지는 않습니다.

기존 `~/venv/.venv`에 LeRobot 의존성이 있어야 합니다. 추가 관리 도구의 의존성은 PyYAML 6.x입니다.
현재 PC에서 PyTorch/실제 체크포인트/장비 없이 가상 검증했으며, Pi의 추론/물리 동작 성능을 검증한 것은 아닙니다.

```bash
cd ~/Logistics_OMX
./omx_control --config params/omx1.yaml validate
./omx_control --config params/omx1.yaml simulate
./omx_control --config params/omx1.yaml plan
```

위 명령은 모터/카메라/MQTT에 연결하지 않습니다. `simulate`의 가짜 홈은 YAML에 저장되지 않습니다.

## YAML을 먼저 확인

`params/omxN.yaml`의 장치 경로와 캘리브레이션 ID를 현장 값으로 확인합니다.

- OMX1: 이전 실행기의 **A 주문→B 원본 모델 / B 주문→A 원본 모델** 매핑을 명시적으로 보존했습니다. 의도한 품목인지 확인하세요.
- OMX2: 이전 C/D 실행기의 `omx_cd_follower_arm` 보정 ID를 사용합니다. Pi의 실제 보정 파일과 확인해야 합니다.
- OMX2 RealSense, OMX3 모터/전면 카메라는 확인되지 않은 값을 `SET_...`로 남겼습니다. 자동 추정하지 않습니다.
- OMX3 기본 모델은 E 250k, OMX4는 F wrist-only 250k의 기존 경로입니다. 공개 E/F 모델과 같은 버전이라고 가정하지 않습니다.
- 파일 경로는 `~`와 환경변수를 지원하며 상대 경로는 저장소 루트 기준입니다.
- `hardware.calibration_file`의 파일명은 `hardware.robot_id`와 일치해야 합니다.
- 카메라 키/해상도는 모델 config와 일치해야 합니다. 체크포인트의 전처리/후처리와 상태 파일도 필요합니다.
- `home.target: null`이 초기값입니다. 임의의 0 자세로 움직이지 않습니다.

이전 `profile.env`의 값이 YAML을 뒤에서 덮어쓰지 않습니다. 필요한 설정을 YAML로 옮기세요.
`device.env`는 기존 장치 ID와 Python 환경 위치를 선택하는 데만 사용합니다.
구형 `lerobot_unified/run_omx_backend.sh`, `run_e_rollout.sh`, `run_f_rollout.sh` 직접 실행은 오류로 종료됩니다.

## 최초 현장 순서 (OMX1부터)

다른 제어 프로세스를 중지한 뒤, 모델이 학습한 초기 자세로 팔을 현장에서 맞춥니다.
이 프로그램은 관측/등록 중 토크를 끄거나 켜지 않습니다. 하중을 지지한 상태에서 기존 장비 절차로 자세를 맞추세요.

```bash
./omx_control --config params/omx1.yaml menu
```

1. YAML의 모델·장치·보정 파일을 확인합니다.
2. 메뉴 **관절 관측**으로 현재 6개 관절값을 확인합니다. 보정값 불일치가 나오면 원래 학습 보정 파일부터 확인합니다.
3. 올바른 초기 자세에서 **현재 자세를 홈으로 등록**합니다. 여러 샘플의 흔들림이 크면 저장을 거절합니다.
4. 토크가 꺼져 있으면 **구동 준비**를 선택합니다. 현재 위치를 목표로 설정한 뒤 토크를 켭니다. 이 기능은 명시적인 MOVE 확인이 필요합니다.
5. **등록 홈으로 제한 이동**을 선택해 도착과 방향을 확인합니다. 허용 접근 영역 밖에서는 이동을 거절합니다.
6. 아래 검사·실행으로 진행합니다.

명령형으로도 사용할 수 있습니다.

```bash
./omx_control --config params/omx1.yaml observe
./omx_control --config params/omx1.yaml capture
./omx_control --config params/omx1.yaml check
./omx_control --config params/omx1.yaml arm --confirm-motion
./omx_control --config params/omx1.yaml home --confirm-motion
./omx_control --config params/omx1.yaml run --confirm-motion
```

`arm`은 등록 홈 근처와 기존 모터 모드(팔 4, 그리퍼 5)에서만 허용됩니다. 모드·영점·방향은 자동 변경하지 않습니다.
토크가 꺼진 상태에서 운용을 시작하면 감독 프로그램은 재시도 대기합니다. 종료 후 도구에서 구동 준비를 먼저 수행하세요.
`home`은 전역 충돌 회피 경로 계획이 아닙니다. 접근 영역과 실제 경로를 확인하고 사용하세요.

실행은 `run_omx_mqtt.sh --robot-id omx1 --confirm-motion` 또는 기존 셸 함수 갱신 후
`omx_start --confirm-motion`로도 가능합니다. `source ~/Logistics_OMX/scripts/omx_shell.sh` 후 `omx_calibrate`로 메뉴를 열 수 있습니다.
OMX2~4는 설정 파일/장치 ID만 해당 값으로 바꿉니다.

## 초기 자세와 카운팅

시작 시 임의 자세를 홈으로 저장하지 않습니다. 등록된 고정 홈에 실제로 도착했는지 확인합니다.
새 피킹 주문, 다음 피킹 사이클, 하차 START에서도 같은 기준 홈을 확인하고 시작합니다.

- `home.arrival_tolerance`: 고정 초기 자세 이동의 도착 기준 (기본 0.8 정규화 단위).
- `home.tolerance`: 사이클 복귀 판정의 관절별 허용값 (기본 7). 초기 자세 도착 조건과 별개입니다.
- `home.max_start_error`: 승인된 홈 접근 영역. 너무 멀면 자동 복귀하지 않습니다.
- `home.move_speed`, `home.max_step`, `home.move_timeout_s`: 홈 이동 제한.
- `home.hold_s`: 피킹 복귀 정지 확인 시간.
- `home.unload_hold_s`: 하차 복귀 정지 확인 시간 (기본 15초).
- `home.max_sample_gap_s`: 연속 관측으로 인정할 최대 공백.
- `counting.leave_tolerance`: 이탈 판정. `min_cycle_s`와 별개로 이탈 이력을 기억합니다.

홈 복귀 후보가 관측되면 새 ACT 추론을 생성하지 않고 관절 정지를 확인합니다.
관측 공백은 유지시간으로 인정하지 않으며 정체는 관절별 진단으로 남깁니다. 기존 12초 near-Home 예외는 새 경로에서 사용하지 않습니다.
한 번 세면 새 이탈 전에는 다시 세지 않습니다. 물리적 이송 성공을 보장하는 카운터는 아닙니다.

설정 변경은 런타임이 멈춘 상태에서만 도구가 허용합니다. 파일을 수동 편집해도 실행 중 설정이 즉시 바뀌는 기능은 없습니다.

```bash
./omx_control --config params/omx1.yaml set --key home.tolerance.wrist_flex --value 5.0
./omx_control --config params/omx1.yaml set --key runtime.cpu_threads --value 3
```

원본 설정은 `params/backups/`에 저장됩니다. 모델/보정/카메라를 바꾸면 홈 연결 해시가 달라져 재등록이 필요합니다.
재등록은 새 모델이 요구하는 물리적 초기 자세를 확인한 뒤 수행합니다.

## 오류와 복구

감독 프로그램이 살아 있는 동안 작업 프로세스의 비정상 종료, 준비시간 초과, heartbeat 정지를 감지해 자식 프로세스 그룹을 정리합니다.
정상 종료 요청 후에도 남으면 제한시간 뒤 강제 종료하며 재시도 간격은 3초부터 최대 60초로 늘어납니다.
카메라와 모터는 각각 정리하므로 하나의 실패가 나머지 반납을 건너뛰지 않습니다.
오류 종료에서 홈으로 임의 이동하지 않습니다. `Ctrl+C`/SIGTERM은 감독 반복을 끝냅니다.
종료 시 토크 정책 기본값은 **유지**입니다. 카메라/포트 반납과 토크 정책은 별개이며 팔의 하중을 고려해야 합니다.

모터 과열·과부하, USB 분리, 보정 불일치는 프로세스만 반복 구동해 해결하지 않습니다. 원인 해소를 기다립니다.
프로세스 강제 종료/전원 단절에서 Python 정리 코드 실행을 보장할 수는 없습니다. 재시작 시 상태·점유·장치를 다시 검사합니다.

실행 기록은 `state/manager/omxN/`에 저장됩니다.

- `joint_errors.json`: 관절별 현재/기준/오차/허용값, 정체 진단.
- `inventory.json`: 현재 주문의 카운트. 재시작 때 지우지 않습니다.
- `jobs.json`: 작업 ID·주문 서명·완료/중단 상태. 같은 명령을 다시 실행하지 않습니다.
- `recovery.json`, `last_stop.json`: 오류 및 종료 기록.

중단된 주문은 실제 물품 위치를 알 수 없으므로 자동 재집기하지 않습니다. 현장 확인 뒤에만 다음을 사용합니다.

```bash
./omx_control --config params/omx1.yaml acknowledge-recovery --confirm-motion
```

기존 수량 기록을 `archive/`로 보존하고 다음 새 작업을 받을 수 있게 합니다. 미완료 ID는 중단 결과로 남겨 재수신해도 재실행하지 않습니다.
**남은 수량은 FMS에서 물리 상태에 맞춰 새 작업 ID로 재등록**해야 합니다.
피킹 시간제한은 수량×단위 제한+로딩 여유로 계산합니다. 하차 동작의 전체 시간제한도 YAML에서 설정합니다.

감독 프로그램 자체가 죽는 상황까지 복구하려면 `deploy/omx-manager@.service` 사용자 서비스를 사용합니다.
이 파일은 저장소에만 제공하며 설치·활성화하지 않았습니다. 최초 보정·소량 검증 후 Pi에서 선택적으로 설치합니다.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/omx-manager@.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start omx-manager@omx1
# 명시적 정지는 자동 재시작되지 않습니다.
systemctl --user stop omx-manager@omx1
```

동일 장치의 터미널 실행과 서비스 실행을 동시에 사용하지 마세요. 전원 복구 후 자동 시작은 별도 운영 결정이므로 enable/linger는 자동 적용하지 않습니다.

## 가상 검증 재실행

모터·카메라·브로커 접속 없이 검사합니다. 테스트용 자식 Python 프로세스만 실행합니다.

```bash
mkdir -p tests/manager_tmp/system
export TMPDIR="$PWD/tests/manager_tmp/system"
export PYTHONDONTWRITEBYTECODE=1
python3 -m pytest tests/test_manager.py tests/test_workstation_protocol.py tests/test_unload_client_protocol.py -q -p no:cacheprovider --basetemp=tests/manager_tmp/pytest
python3 scripts/test_omx_mqtt_faults.py --runs 10
python3 scripts/verify_omx.py --runs 5
```

가상 시험은 실제 PyTorch 모델 출력·USB 장치·모터 하중·현장 충돌 경로를 검증한 것이 아닙니다.
냉각팬 없는 Pi5 4GB/4A 전원의 실제 온도·클럭·메모리·추론 지연은 현장에서 측정해야 합니다.
