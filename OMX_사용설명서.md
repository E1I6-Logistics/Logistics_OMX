# OMX 통합 사용 설명서 — Raspberry Pi 5 4GB

기준: OMX 저장소 `dev` 브랜치. 팔마다 Pi 5 4GB 한 대를 사용합니다.
리더암 없이 기존 모터 보정 JSON과 모델의 초기 자세를 사용합니다.
이 문서의 명령은 OMX 저장소 루트에서 실행합니다. `omx1`은 해당 팔 번호로 바꾸세요.
실제 Pi·카메라·모델 추론 성능과 systemd 재시작은 현장에서 별도 확인해야 합니다.

## 1. 코드와 Python 환경 확인

```bash
git status
git fetch origin
git switch dev
git pull --ff-only
# 추적 브랜치가 없다면 한 번만 설정
# git branch --set-upstream-to=origin/dev dev
```

작업 중 변경이 있으면 pull 전에 내용을 보존하세요. 강제 초기화나 보정 파일 삭제를 하지 마세요.
`omx_control`은 기본적으로 `~/venv/.venv/bin/python`을 사용하며 없으면 `python3`를 사용합니다.
다른 환경은 `export OMX_PYTHON=/실제/venv/bin/python`으로 선택합니다.
PyYAML 6.x와 기존 LeRobot 의존성이 필요하며, 가상 테스트에는 pytest가 필요합니다.
실행 스크립트가 패키지를 자동 설치하거나 시스템을 변경하지는 않습니다.

## 2. 현장 설정 파일 만들기

배포 템플릿은 `params/omx1.yaml`~`omx4.yaml`입니다. 모든 파라미터에 한글 설명이 있습니다.
현장 설정은 Git 관리에서 제외되는 `state/config/`에 두면 업데이트와 보정을 분리할 수 있습니다.

```bash
OMX_TEST_ID=omx1
mkdir -p state/config
# 이미 파일이 있으면 덮어쓰지 않습니다.
cp -n "params/${OMX_TEST_ID}.yaml" "state/config/${OMX_TEST_ID}.yaml"
OMX_TEST_CFG="state/config/${OMX_TEST_ID}.yaml"
```

기존에 홈 등록·수정을 마친 파일이 있다면 그 파일을 보존해서 사용하세요.
새 터미널마다 위 ID/CFG 변수를 다시 지정하거나 전체 경로를 직접 입력합니다.
`--robot-id`와 설정 파일 안의 `device_id`가 다르면 실행 전에 거절합니다. 시작 전 `plan`에서도 확인하세요.
`run_omx_mqtt.sh --robot-id omxN`은 `state/config/omxN.yaml`이 있으면 우선 사용하고 없으면 배포 템플릿을 사용합니다. `--config`로 다른 파일을 명시할 수도 있습니다.

| 장비 | 설정 확인 |
|---|---|
| OMX1 | A/B 반대 모델 매핑은 사용자가 확인한 정상 값. 그대로 유지 |
| OMX2 | 실제 RealSense 시리얼, `omx_cd_follower_arm` 보정 파일 |
| OMX3 | 모터 by-id 경로, 상단 카메라 경로, E 250000 체크포인트 |
| OMX4 | 상단·손목 640×480 입력, F v3_5 230000 체크포인트 |
| 공통 | 브로커 IP/포트, 모델·processor 파일, 보정 파일 ID·실제 카메라 |

`SET_...`는 현장 값으로 채워야 합니다. 모델 가중치와 개인 보정 파일은 소스에 포함되지 않습니다.
위치값은 **각도가 아닌 정규화 단위**입니다. 모터 영점/방향/모드 재교정은 이 도구의 기능이 아닙니다.

## 3. 전체 관리 명령

공통 형식: `./omx_control --config "$OMX_TEST_CFG" 명령 [옵션]`

| 명령 | 용도·영향 |
|---|---|
| `menu` | 대화형 설정·홈 등록 메뉴 |
| `validate` | YAML 값과 형식 검사. 장비 미접속 |
| `simulate` | 가짜 팔의 8회 사이클 검증. 실제 팔·홈 파일 변경 없음 |
| `plan` | 모델·카메라·실행할 명령 확인. 장비 미접속 |
| `check` | 실제 모델/processor 파일·보정 구조·홈 연결 해시 검사. 모터/카메라 통신 검사는 아님 |
| `observe` | 약 2초 동안 6개 관절 관측. 목표값·토크 변경 없음 |
| `capture` | 안정된 현재 자세를 설정 파일의 홈으로 저장. 모터 보정 JSON 변경 없음 |
| `arm --confirm-motion` | 현재 위치를 목표로 넣고 토크 켜기. 토크가 꺼져 있을 때 필요 |
| `home --confirm-motion` | 등록 홈 근처의 허용 영역에서 제한 이동. 실제 팔 제어 |
| `set --key 키 --value 값` | 허용된 파라미터 저장. 실행 중 변경 차단, 다음 실행부터 적용 |
| `run --confirm-motion` | 모델·장치·MQTT 실행과 자식 프로세스 감독. 실제 팔 제어 |
| `logs` | 저장된 중요 로그 최근 50줄 |
| `logs --follow` | 중요 로그 실시간 보기. 로봇에 명령을 보내지 않음 |
| `monitor` | CPU·메모리·온도와 선택적 카메라/제어/ACT 성능 조회 |
| `acknowledge-recovery --confirm-motion` | 물품 확인 후 중단 상태 보관·정리. 팔 이동은 하지 않지만 실행 재개 조건을 바꿈 |
| `service --action 동작` | systemd 자동 재시작 서비스 관리. 아래 별도 절차 참고 |

`--confirm-motion`은 워크스페이스·모델·초기 자세를 확인한 뒤 실제 구동을 요청한다는 표시입니다.
`capture`는 파일을 저장합니다. 기존 설정은 같은 폴더의 `backups/`에 보존되고 한글 파라미터 주석도 다시 생성됩니다.
모델·보정·카메라 변경은 홈 연결 해시를 바꾸므로 초기 자세를 다시 확인하고 등록해야 합니다.
진단·서비스 파라미터 변경만으로 물리 홈이 무효화되지는 않습니다.

## 4. 최초 설정과 홈 테스트

기존 제어 프로그램을 중지하고 팔의 작업 공간을 비웁니다. 모델이 학습한 초기 자세를 기존 장비 절차로 맞춥니다.
토크가 켜진 팔을 억지로 밀지 마세요. 준비/관측 도구는 임의로 토크를 끄지 않습니다.

```bash
./omx_control --config "$OMX_TEST_CFG" validate
./omx_control --config "$OMX_TEST_CFG" simulate
./omx_control --config "$OMX_TEST_CFG" plan
./omx_control --config "$OMX_TEST_CFG" observe
./omx_control --config "$OMX_TEST_CFG" capture
./omx_control --config "$OMX_TEST_CFG" check
# 토크가 꺼져 있다면 수행
./omx_control --config "$OMX_TEST_CFG" arm --confirm-motion
./omx_control --config "$OMX_TEST_CFG" home --confirm-motion
```

처음 `home_registered=False`는 정상이고 simulate는 `counted_cycles=8`이어야 합니다.
홈 등록·검사는 모델 파일의 전체 해시를 읽으므로 저장장치 속도에 따라 시간이 걸릴 수 있습니다.
홈 이동은 전역 충돌 회피 경로가 아닙니다. 처음에는 홈 근처에서 확인하세요.
보정 불일치·모터 오류·범위 초과·관측 공백·허용 접근 영역 초과는 제한값을 무작정 늘려 넘기지 마세요.
메뉴로 하려면 `./omx_control --config "$OMX_TEST_CFG" menu`를 사용합니다.

## 5. 실행·중요 로그·성능 확인

```bash
# 터미널 1: 중요 로그 화면만. 상세 계측은 기본 꺼짐
./run_omx_mqtt.sh --robot-id "$OMX_TEST_ID" --config "$OMX_TEST_CFG" --confirm-motion --important-only

# 성능을 측정할 때는, 이전 실행을 종료한 다음 이 명령 사용
./run_omx_mqtt.sh --robot-id "$OMX_TEST_ID" --config "$OMX_TEST_CFG" --confirm-motion --important-only --diagnostics

# 별도 터미널: 같은 CFG를 지정
./omx_control --config "$OMX_TEST_CFG" monitor
./omx_control --config "$OMX_TEST_CFG" logs --follow
# 저장된 최근 로그만
./omx_control --config "$OMX_TEST_CFG" logs --lines 100
# 유한한 성능 기록: 기본 5초 간격으로 60회
mkdir -p logs
./omx_control --config "$OMX_TEST_CFG" monitor --samples 60 --json > "logs/${OMX_TEST_ID}_perf.jsonl"
```

Pi 5 4GB 기본값은 CPU 연산 스레드 2개, 상세 계측 비활성, 스냅샷 5초, 유효기간 15초입니다.
`diagnostics.important_only: true`를 파일에 저장하면 실행 때 옵션을 반복 입력하지 않아도 됩니다.
`diagnostics.enabled: true`는 상세 계측을 기본 활성화합니다. CLI 활성 옵션은 해당 실행에만 적용됩니다.

| 표시 | 해석 |
|---|---|
| CPU | rollout 프로세스의 구간 사용률. 100%=코어 하나, 100% 초과 가능 |
| totalCPU | 시스템 전체 사용률 0~100% |
| RSS / RAM_available | rollout 메모리 / 시스템 가용 메모리 |
| temp / clock | Pi 온도·CPU 클럭. 미지원이면 `--` |
| loop / target | 관측·대기 확인을 포함한 루프 시작 Hz / 목표 Hz |
| model_send | 정책 목표를 성공적으로 전송한 Hz. 모터 내부 제어 주파수가 아님 |
| ACT_chunk | 최근 구간에 새 청크를 생성한 평균 시간. 캐시 액션 꺼내기는 제외 |
| worst / samples | 현재 프로세스에서 측정한 청크 최대 시간 / 호출 수 |
| camera capture | SDK/OpenCV에서 성공적으로 받은 프레임/프레임셋 FPS |
| camera published | 후처리 후 영상 버퍼에 게시한 FPS |
| camera age | 마지막 영상 버퍼 게시 뒤 경과 시간 |

첫 두 스냅샷 전, 구간 내 청크 생성 없음은 `--`가 정상일 수 있습니다.
OMX3/4 READY 대기에서 loop/model_send가 0이어도 정상일 수 있습니다.
같은 영상을 반복 읽어도 카메라 FPS는 증가하지 않으며 RealSense color/depth를 중복 계산하지 않습니다.
깨진·오래된·다른 프로세스의 계측값은 현재 성능으로 표시하지 않습니다.
청크 시간은 현재 CPU 관리 실행 기준이며 GPU 동기화 계측은 하지 않습니다.

- 전체 로그: `logs/omxN/full.log`, 파일당 2MiB + 이전 1개.
- 중요 로그: `logs/omxN/important.log`, 파일당 1MiB + 이전 1개.
- 상세 계측: `state/manager/omxN/metrics.json`, 최신 값만 교체.
- 반복 IDLE/WORKING heartbeat는 중요 화면에서 제외하고 반복 주기 경고는 기본 10초마다 표시합니다.
- 로그 큐는 기본 128줄이며 각 조각은 최대 16KiB입니다. 저장이 느려 큐가 차면 로그 일부를 버리고 누락 수를 남깁니다.
- 제어/영상 스레드는 이미지를 복사·저장하거나 매 프레임 파일을 쓰지 않습니다. 별도 스레드가 작은 스냅샷만 저장합니다.

모니터 Ctrl+C는 로봇을 종료하지 않습니다. 상세 계측을 끄려면 작업 완료 후 런타임 종료, YAML `enabled:false`와 옵션 생략으로 재실행합니다.
온도/메모리/청크 지연/프레임 나이를 같이 비교하세요. 30Hz 미달 경고 하나만으로 카메라 고장이라고 단정하지 마세요.
모델 학습 FPS·영상 입력 크기를 바꾸는 것은 단순 성능 설정과 다르므로 임의로 바꾸지 마세요.

## 6. FMS 주문 테스트 순서

FMS 가상 모드는 실제 OMX를 호출하지 않습니다. 실물 연동은 FMS에서 **실물 + AI**를 선택합니다.
각 팔별 단독 준비 검사 후, AMR 도착·정렬을 거쳐 정상 주문을 보냅니다.
실물 AI 모드의 경로·작업대 선택은 AI이며, 로봇 배차까지 KEV로 하려면 FMS의 `FMS_REAL_KEV_DISPATCH_ENABLED=1` 설정이 별도로 필요합니다.

1. 해당 픽업 품목 1개: 실제 집기 → 홈 복귀 → 카운트 1 증가 → 프로세스 생존.
2. 같은 품목 2개: 사이클마다 1회, 정확히 2개에서 대기.
3. OMX1 A/B 또는 OMX2 C/D 혼합: 모델 전환·수량·진행률 확인.
4. 새로운 주문 ID로 1개 주문 5~10회: 다음 주문 접수와 반복 복귀 확인.
5. 여러 물품 하차: OMX3/4가 전부 내렸는지 직접 확인.
6. 주문 완료 후 Ctrl+C 종료·재실행: 포트/카메라 점유 오류와 완료 주문 재실행이 없어야 함.

현재 카운트는 물품 성공 센서가 아니라 홈 이탈→복귀→연속 정지 기준입니다.
OMX3/4는 기본 홈 정지 15초 후 완료하며, 명령 한 건당 하차 시퀀스 한 번입니다. 물품이 남았는데 완료로 표시되면 테스트 실패로 기록하세요.
종료 시 토크 기본값은 유지입니다. 파일·포트 반납과 토크 해제는 별개입니다.

## 7. 강제 종료·OOM 후 자동 재시작

터미널 실행은 감독 프로그램이 살아 있는 동안 자식 rollout/MQTT를 감시·정리·재시도합니다.
**감독 프로그램 자체가 SIGKILL/OOM으로 죽는 경우까지 복구하려면 Pi에서 서비스를 설치·시작해야 합니다.**
설치 도구는 현재 저장소·설정 파일의 실제 절대 경로로 장비별 사용자 unit을 만듭니다. 기존 `device.env`의 Python 환경 설정도 실행기를 통해 읽습니다. PC에서는 실제 설치하지 않았습니다.

```bash
# 최초 홈·소량 동작 검증을 마친 뒤, 기존 터미널 실행은 종료합니다.
./omx_control --config "$OMX_TEST_CFG" service --action install
./omx_control --config "$OMX_TEST_CFG" service --action start --confirm-motion
./omx_control --config "$OMX_TEST_CFG" service --action status
./omx_control --config "$OMX_TEST_CFG" service --action logs --follow
# 사용자가 정지: 자동 재시작하지 않음
./omx_control --config "$OMX_TEST_CFG" service --action stop
```

기본 `Restart=always`, 재시작 대기 5초, 프로세스 그룹 정리, `OOMPolicy=stop`을 사용합니다.
실제 서비스 데몬 동작은 Pi에서 확인해야 합니다. 프로세스 재실행이 전원·USB·모터 하드웨어 오류까지 해결하지는 않습니다.
강제 종료/전원 차단에서는 Python 정리 코드 실행을 보장할 수 없으며, 종료된 프로세스의 OS 자원 반납과 장치 재점검으로 복구합니다.

사용자 세션 자동 시작 등록은 명시적으로 요청할 때만 수행합니다.

```bash
./omx_control --config "$OMX_TEST_CFG" service --action enable --confirm-motion
./omx_control --config "$OMX_TEST_CFG" service --action disable
# 로그인 전·로그아웃 후에도 사용자 서비스를 유지하려면 Pi에서 별도로 설정
# loginctl enable-linger "$USER"
# 서비스 삭제: 정지·자동 시작 해제 후 생성한 unit만 제거
./omx_control --config "$OMX_TEST_CFG" service --action uninstall
```

또는 YAML `service.enable_on_boot:true` 뒤 install에 `--confirm-motion`을 붙일 수 있습니다.
`stop`은 현재 실행 정지이고, `disable`은 이후 사용자 세션 자동 시작 해제입니다.
직접 터미널 실행과 서비스 실행을 동시에 하지 마세요.
서비스 재시작/종료 설정을 바꾸거나 저장소·설정 파일을 이동하면 install을 다시 수행합니다.

현장 재시작 시험은 **주문이 없는 대기 상태에서만** 다음 순서로 합니다.

```bash
systemctl --user show "omx-manager@${OMX_TEST_ID}.service" -p MainPID --value
# 위 PID가 해당 OMX 감독 프로세스인지 확인한 뒤, 테스트 목적으로만 강제 종료
# kill -KILL 확인한_PID
# 재시작 대기 뒤 status에서 새 MainPID·준비 상태 확인
./omx_control --config "$OMX_TEST_CFG" service --action status
```

작업 중 죽었다면 프로그램은 재기동을 시도하지만 미완료 작업은 자동 재집기하지 않습니다.
실제 물품과 FMS 주문을 확인한 뒤 서비스를 stop하고 다음을 수행합니다.

```bash
./omx_control --config "$OMX_TEST_CFG" acknowledge-recovery --confirm-motion
./omx_control --config "$OMX_TEST_CFG" service --action start --confirm-motion
```

기존 기록은 보존하며 미완료 ID는 중단 결과로 남깁니다. 남은 수량은 FMS에서 **새 작업 ID**로 등록합니다.
작업 상태 파일을 지우거나 같은 작업을 무작정 재전송하지 마세요.

## 8. 파라미터 변경·백업

실행과 서비스가 멈춘 상태에서 수정합니다. 파일 편집 또는 허용 항목은 `set`을 사용합니다.

```bash
./omx_control --config "$OMX_TEST_CFG" set --key runtime.cpu_threads --value 2
./omx_control --config "$OMX_TEST_CFG" set --key home.tolerance.wrist_flex --value 5.0
./omx_control --config "$OMX_TEST_CFG" set --key diagnostics.enabled --value true
./omx_control --config "$OMX_TEST_CFG" set --key diagnostics.important_only --value true
./omx_control --config "$OMX_TEST_CFG" set --key diagnostics.interval_s --value 2
# interval=2에는 stale>=4, 기본 stale=15도 가능
./omx_control --config "$OMX_TEST_CFG" validate
```

허용 `set` 키는 `models.active_profile`, `runtime.cpu_threads/fps`,
`home.hold_s/unload_hold_s/still_delta/max_start_error/move_speed/tolerance.관절명`,
`counting.min_cycle_s/stall_s/leave_tolerance`, `diagnostics.*`, `service.*`입니다.
모델·카메라·포트·보정 경로는 한글 주석을 읽고 YAML을 직접 검토해서 바꿉니다.
기존 JSON 영점/방향을 이 도구로 재교정하지 않습니다. INT8 프로필은 자리만 있고 실행은 지원하지 않습니다.
`stale_s`는 `interval_s`의 2배 이상이어야 합니다. 서비스 종료 시간은 자식 두 개의 정리 시간을 확보해야 합니다.

## 9. 가상 검증과 로그 전달

아래 도구는 실제 모터·카메라·MQTT·systemd 데몬에 연결하지 않습니다.
PyTorch 모델을 실제로 추론하지 않으며 가짜 프로세스·파일·카운터로 논리를 확인합니다.

```bash
bash scripts/verify_manager.sh
python3 scripts/repeat_manager_validation.py --runs 20
```

반복 검증은 기본 20회이며 서로 다른 시드와 독립 Python 프로세스로 수행합니다. 추가 반복은 별도 요청이 있을 때 수행합니다.
결과는 `reports/repeated_validation.json`, 각 회차 로그는 `reports/repeated_validation/`에 저장합니다.
실물 성능 수치나 모델 정확도를 검증한 결과로 해석하지 마세요.

오류가 나면 최초 오류와 traceback을 포함한 `logs/omxN/full.log`, `important.log`와 다음 파일을 보존합니다.

- `state/manager/omxN/joint_errors.json`: 관절별 현재·목표·오차·허용값.
- `inventory.json`: 현재 수량. `jobs.json`: 작업 ID·중복 방지·중단 기록.
- `recovery.json`, `last_stop.json`: 복구 시도·종료 정보.
- `metrics.json`: 최근 성능 스냅샷. 없어도 계측 비활성이면 정상.

진단 파일 저장 실패는 가능한 한 제어와 분리하지만, 주문 기록·실제 모터 통신 오류는 정상 동작으로 숨기지 않습니다.


## 10. observe에서 연결 오류가 발생했던 장비 업데이트

`observe`에서 연결 직후 `DynamixelMotorsBus is not connected`가 발생하거나,
연결 실패 뒤 `'NoneType' object has no attribute 'close'`가 나타나던 관리 도구 버그를 수정했습니다.
관측 세션이 끝날 때까지 로봇 객체를 유지하고 열린 포트만 닫으며, 정리 실패가 최초 오류를 가리지 않도록 했습니다.
이 수정은 실제 모터 오류나 잘못된 장치 경로를 정상으로 처리하지 않습니다.

```bash
# 기존 제어 프로그램/서비스를 종료한 다음 OMX 저장소에서 실행
# 로봇에 단독 clone했다면 보통 ~/Logistics_OMX
cd ~/Logistics_OMX
git status
git switch dev
git pull --ff-only origin dev
# 해당 팔 번호로 변경. 기존 state/config 파일을 다시 복사하거나 지우지 않습니다.
./omx_control --config state/config/omx1.yaml observe
```

업데이트 후 OMX3에서 최초 연결 오류가 표시되면 `hardware.port`를
실제 `/dev/serial/by-id/` 경로와 다시 비교하세요. 예전 close 오류만으로 최초 원인은 확정할 수 없습니다.
`observe`는 카메라 영상 수신이나 모델 추론을 검증하지 않습니다.

## 11. 다음 주문에서 홈 정렬이 시간초과되는 경우

홈 정렬은 카운트 판정과 다릅니다. 기본 `home.tolerance`의 관절별 7.0은 작업 종료 판정이고,
`home.arrival_tolerance:0.8`은 다음 모델 실행 전 고정 초기 자세의 도착 기준입니다.
모터가 작은 목표 차이에 반응하지 않는 경우, 기존의 실측 위치 기준 미세 목표는 계속 같은 근처에 머물 수 있었습니다.
현재 코드는 이전에 보낸 목표를 기준으로 제한 속도로 전진시키며, 실측 위치와의 차이가
`home.max_following_error`를 넘는 명령은 보내지 않습니다. 관측이 실제로 이 제한을 넘거나 접근 영역을 벗어나면 정지합니다.
기존 속도·최대 스텝·최종 도착 오차·20초 제한·모터 보정은 자동 완화하지 않습니다.
기존 현장 YAML에 새 항목이 없으면 추종 오차 제한은 3.0을 사용합니다. 위치 단위는 도가 아닌 정규화 값입니다.
홈 정렬에는 관절만 읽고 카메라 영상 복사는 하지 않습니다.

오류가 발생하면 `state/manager/omxN/home_motion.json`에 단계, 실제 관절값, 목표, 최종 도착 오차,
카운트 허용값, 보낸 목표와 추종 오차가 남습니다. 작은 최신 스냅샷을 최대 1초마다 교체하며
STARTING/ARRIVED/FAILED/STOPPED 로그만 중요 화면에 표시합니다. 오류 메시지에도 실패 관절이 나옵니다.
진단 파일 쓰기 실패가 이동·정리 처리를 막지 않습니다.

현장 업데이트/재검증 순서:

```bash
# 각 Pi에서 기존 실행/서비스를 먼저 종료하고 OMX 저장소로 이동
cd ~/Logistics_OMX
git switch dev
git pull --ff-only origin dev
# 해당 장비 번호로 변경
OMX_TEST_ID=omx1
OMX_TEST_CFG="state/config/${OMX_TEST_ID}.yaml"
./omx_control --config "$OMX_TEST_CFG" check
# 물품과 미완료 주문을 확인·정리한 뒤 중단 작업 기록 보관/해제
./omx_control --config "$OMX_TEST_CFG" acknowledge-recovery --confirm-motion
# 홈 접근 영역과 작업 공간을 확인한 뒤 실제 제한 이동 시험
./omx_control --config "$OMX_TEST_CFG" home --confirm-motion
cat "state/manager/${OMX_TEST_ID}/home_motion.json"
```

`capture`로 현재 자세를 무조건 덮어쓰면 학습 초기 자세와 달라질 수 있습니다. 기존 등록 홈은 보존하세요.
`home`가 실패하면 `home_motion.json`의 관절별 오차를 먼저 확인합니다. 시간/속도/접근 영역을 무작정 늘리지 마세요.
`set`에서 `home.arrival_tolerance`, `home.move_timeout_s`, `home.max_step`,
`home.max_following_error`, `home.max_sample_gap_s`도 변경할 수 있습니다.

카운트가 안 된 경우에는 `joint_errors.json`을 확인합니다. `ok:false`인 관절이 하나라도 있으면 완료가 아닙니다.
최근 관측 공백/정지/홈 이탈/연속 정지 시작 시각도 기록합니다. 모델 추론 중 긴 공백 뒤 홈 영역에 진입하면
다음 ACT 청크 생성을 잠시 보류하고 새 관절 관측으로 연속 정지를 확인합니다. 관측 공백 자체를 정지 시간으로 세지 않습니다.

현장 OMX1 사례: 손목 굽힘 오차 7.863이 허용값 7.0을 넘어 카운트가 거절되었습니다.
실제 복귀 자세가 올바른 작업 종료 자세인지 확인한 경우에만 해당 관절 허용값을 좁은 범위에서 조정합니다.
예를 들어 9.0은 그 관측값을 포함하지만, 물품 이송 성공을 검증해 주지는 않습니다. 다른 팔에 일괄 적용하지 않습니다.

```bash
# OMX1에서 복귀 자세를 확인한 뒤에만 적용하는 시험값
./omx_control --config state/config/omx1.yaml set --key home.tolerance.wrist_flex --value 9.0
```

`Control loop is running slower (0.5 Hz)`는 느린 한 주기의 역수일 수 있으며 전체 구간 평균이 아닙니다.
ACT의 청크 생성 지연과 평균 동작 전송률은 별도로 `monitor`에서 봅니다.
예를 들어 추론 32회/동작 전송 3200개는 청크당 100개 실행이며,
추론 합계 68.911초라면 청크당 평균 약 2.153초입니다. 이것만으로 모든 제어 주기가 2초라고 판단하지 않습니다.

## 12. FMS 없이 CLI로 실물 주문 시험

FMS 애플리케이션과 AMR 없이도 MQTT 브로커만 접근 가능하면 됩니다.
물품·바구니·컨베이어를 학습 때 위치에 준비하고 해당 OMX가 대기 중일 때 별도 터미널에서 한 명령씩 보냅니다.
실제 팔이 움직입니다. `mosquitto_pub`가 없다면 `mosquitto-clients` 패키지가 필요합니다.

```bash
# OMX1 A 1개. B 시험은 A를 B로 변경
mosquitto_pub -h 192.168.0.16 -p 1883 -t omx1/command -q 1 -m "{\"job_id\":\"cli-$(date +%s%N)\",\"items\":{\"A\":1}}"
# OMX2 C 1개. D 시험은 C를 D로 변경
mosquitto_pub -h 192.168.0.16 -p 1883 -t omx2/command -q 1 -m "{\"job_id\":\"cli-$(date +%s%N)\",\"items\":{\"C\":1}}"
# OMX3/4는 물품 1개 보장이 아닌 하차 작업 시작
mosquitto_pub -h 192.168.0.16 -p 1883 -t omx3/command -q 1 -m "{\"job_id\":\"cli-$(date +%s%N)\",\"items\":{\"E\":1}}"
mosquitto_pub -h 192.168.0.16 -p 1883 -t omx4/command -q 1 -m "{\"job_id\":\"cli-$(date +%s%N)\",\"items\":{\"F\":1}}"
# 다른 터미널에서 OMX1의 접수/진행/결과 확인. 다른 팔은 번호 변경
mosquitto_sub -h 192.168.0.16 -p 1883 -v -t omx1/ack -t omx1/progress -t omx1/result
```

브로커 주소/포트는 각 현장 YAML과 일치시킵니다. 새 실행마다 새 작업 ID를 생성하며 retain 옵션은 사용하지 않습니다.
접수 후 완료 결과를 기다린 뒤 다음 주문을 보냅니다. 같은 작업 ID 재전송은 재동작을 의미하지 않습니다.
1개 주문 완료 뒤 새로운 ID로 1개 주문을 5회 이상 반복해 다음 작업 전 홈 정렬과 대기를 확인합니다.
