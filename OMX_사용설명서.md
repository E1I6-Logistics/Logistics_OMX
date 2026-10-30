# OMX 통합 사용 설명서 — Raspberry Pi 5 4GB

기준: OMX 저장소 `dev` 브랜치. 팔마다 Pi 5 4GB 한 대를 사용합니다.
리더암 없이 기존 모터 보정 JSON과 모델의 초기 자세를 사용합니다.
이 문서의 명령은 OMX 저장소 루트에서 실행합니다. `omx1`은 해당 팔 번호로 바꾸세요.
실제 Pi·카메라·모델 추론 성능과 systemd 재시작은 현장에서 별도 확인해야 합니다.

**현재 권장 절차는 15절입니다.** OMX1~4 모두 pick/place 필수 조건을 제거했습니다. 수동으로 팔 자세를 맞추는 대신 모델을 실행하여 정상 복귀 영역을 기록합니다. 고정 초기 홈과 카운팅용 복귀 영역은 별개입니다. OMX3/4의 빈 바구니 기준·실물 시야 검증과 기존 복귀 경로 시험은 여전히 필요합니다. 14절의 수동 작업 단계 등록은 이전 방식 설명입니다.

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

## 13. Pi별 Bash 단축 명령 등록

각 Pi에서 한 번만 실행합니다. `omx01/omx02/omx03/omx4` 호스트 이름을 각각
`omx1/omx2/omx3/omx4`로 자동 인식합니다. 기존 등록과 이름이 충돌하면 자동 변경하지 않고 오류를 표시합니다.

```bash
cd ~/Logistics_OMX
git pull --ff-only origin dev
bash scripts/install_omx_shell.sh
source ~/.bashrc
omx_info
omx_help
```

자동 식별이 안 되면 해당 번호를 명시합니다.

```bash
bash scripts/install_omx_shell.sh --robot-id omx4
# 사용자 지정 현장 YAML/가상환경이 있다면 명시
# bash scripts/install_omx_shell.sh --robot-id omx4 --config state/config/omx4.yaml --python "$HOME/venv/.venv/bin/python"
source ~/.bashrc
```

번호·현장 YAML·Python 경로는 `~/.config/logistics_omx/shell.env`에 저장하고 `.bashrc`에 source 블록 한 개만 등록합니다.
기존 `.bashrc`는 변경 전에 백업하며, 재설치 시 같은 블록을 중복 추가하지 않습니다.
기존 `state/config/omxN.yaml`은 보존합니다. 없을 때만 배포 템플릿에서 복사합니다.
모델·모터 보정·기존 가상환경은 바꾸지 않습니다. 설치/터미널 시작만으로 장치나 서비스가 구동되지는 않습니다.
매번 가상환경을 활성화하거나 저장소로 이동할 필요 없이 아래 명령을 사용할 수 있습니다.

| 단축 명령 | 동작 |
|---|---|
| `omx_start` | 해당 팔 실행, 중요 로그 표시. 종료는 이 터미널에서 Ctrl+C |
| `omx_start --diagnostics` | 카메라/제어/ACT 성능 계측을 켜고 실행 |
| `omx_home` | 등록 홈 근처에서 제한 복귀. 실제 팔 이동 |
| `omx_arm` | 홈 접근 범위 안에서 현재 위치 목표를 설정하고 토크 ON |
| `omx_release` | 설정된 대기 시간 후 전체 토크 OFF. 물품을 내려놓고 팔 지지 필수 |
| `omx_observe` | 관절값·등록 홈 오차 확인 |
| `omx_capture` | 현재 안정 자세를 홈으로 저장. 복귀 명령이 아님 |
| `omx_check` | 파일·모델 I/O·등록 홈 검사 |
| `omx_calibrate` | 대화형 설정/보정 메뉴 |
| `omx_status` | 상태·성능 한 번 조회 |
| `omx_monitor` | 성능 연속 조회. Ctrl+C는 모니터만 종료 |
| `omx_logs --follow` | 중요 로그 실시간 조회 |
| `omx_recover` | 실제 물품 확인 후 중단 작업 기록 보관/해제 |
| `omx_order A 1` | 해당 팔에 수동 MQTT 실물 주문. FMS 없이 브로커만 필요 |
| `omx_service install` | 자동 재시작 서비스 설치만 수행 |
| `omx_service start` | 설치한 서비스 시작. 실제 팔 구동 가능 |
| `omx_service stop` | 서비스를 사용자가 정지. 자동 재시작하지 않음 |
| `omx_service status` | 서비스 상태 |
| `omx_info`, `omx_help` | 현재 등록과 명령 목록 |
| `omx_cd` | 저장소로 이동 |

품목은 OMX1 A/B, OMX2 C/D, OMX3 E, OMX4 F입니다. `omx_order`는 내부에서 `mosquitto_pub`를 호출하고
YAML의 브로커 주소/포트를 사용하며 매번 새 작업 ID를 생성합니다. 완료 후 다음 주문을 보냅니다.
OMX3/4의 수량 1은 하차 작업 시작용이며 물품 1개 하차를 보장하지 않습니다. 다른 수량은 수동 명령에서 거절합니다.

`omx_release`는 현재 자세가 홈에서 멀어도 사용 가능합니다. 기본 대기는 10초이며
`recovery.release_delay_s`로 3~60초 범위에서 설정할 수 있습니다. 토크를 끄기 전에 물품을 내려놓고 팔을 받칩니다.
대기 중 Ctrl+C로 취소할 수 있습니다. 6개 관절의 토크 OFF 확인 후에만 수동으로 자세를 맞춥니다.
다음으로 팔을 지지한 상태에서 `omx_arm`, 팔이 안정적으로 버티면 손을 치우고 `omx_home` 순서로 복귀합니다.
기존 모터 영점·방향·범위·Goal Position을 토크 해제 명령이 재설정하지 않습니다.
구형 `omx_reset`의 직접 재고 초기화는 제거했습니다. 중단 작업은 `omx_recover`를 사용합니다.

GitHub 접속이 안 되지만 같은 LAN에서 다른 Pi에 접속 가능한 경우, 업데이트된 OMX2에서 증분 Git bundle을 보낼 수 있습니다.
아래 예시는 OMX1의 현재 커밋이 `4116f5f`인 경우이며 실제 현재 버전을 먼저 확인하세요.

```bash
# 업데이트된 OMX2에서 실행. 수신지는 실제 OMX1 IP와 사용자명 사용
cd ~/Logistics_OMX
git bundle create /tmp/omx-update.bundle 4116f5f..dev
scp /tmp/omx-update.bundle omx01@192.168.0.151:/tmp/omx-update.bundle
# OMX1에서 실행
cd ~/Logistics_OMX
git bundle verify /tmp/omx-update.bundle
git pull --ff-only /tmp/omx-update.bundle dev
# 이후 위 단축 명령 설치 절차 수행
```

이 방식은 origin의 추적 정보는 갱신하지 않습니다. 인터넷이 복구된 뒤 `git fetch origin`으로 동기화합니다.


## 14. 작업 단계·여러 물건 하차·등록 경로 복구

### 14-1. 적용과 기존 설정 보존

이 버전은 모델/가중치/모터 영점 JSON을 변경하지 않습니다. 모델 양자화도 포함하지 않습니다. 홈에 잠깐 접근했을 때 모델 전송을 중단하던 처리를 없애고, 등록한 실제 작업 단계가 순서대로 관측되어야 홈 복귀를 작업 사이클로 인정합니다.

각 Pi에서 실행 중인 프로그램은 Ctrl+C, 서비스는 `omx_service stop`으로 멈춥니다. 아래 과정에서는 `params`를 현장 YAML 위에 복사하거나 홈을 다시 `capture`하지 않습니다.

```bash
cd ~/Logistics_OMX
git pull --ff-only origin dev
bash scripts/install_omx_shell.sh
source ~/.bashrc
omx_info
omx_setup
```

`omx_setup`은 누락된 `workflow`, `unload`(3/4), `recovery.routes` 설정만 추가합니다. 기존 설정값과 홈·카메라·모델 매핑을 유지하고 YAML 백업을 남깁니다. OMX1 A/B 모델 반대 매핑도 유지합니다. 상세 명령은 `./omx_control --config state/config/omxN.yaml ...`이며 아래 단축 명령은 해당 Pi 번호와 파일을 자동 사용합니다.

**미등록이면 `omx_start`는 RECOVERY_WAIT로 들어갑니다.** 모델을 로딩하지 않고 MQTT로 ERROR/RECOVERY_WAIT를 알리며 새 주문에는 accepted=false를 보냅니다. 등록 작업을 하려면 감독 프로그램을 먼저 멈추세요. 장치 잠금을 유지하기 때문에 별도 프로세스가 동시에 모터를 열 수 없습니다.

### 14-2. 팔로워만으로 실제 작업 단계 등록

리더암과 추가 학습은 필요 없습니다. 각 모델이 실제로 거치는 집기 자세와 놓기 자세를 최소 두 개, 작업 순서대로 등록합니다. 홈과 이웃 단계의 허용 영역이 겹치면 구동을 거절합니다. 등록값은 정규화 위치 단위이며 각도가 아닙니다.

팔을 손으로 배치할 때는 물건을 먼저 내리고 팔을 지지한 뒤 `omx_release`로 토크 OFF를 확인합니다. 구동 중이거나 토크 ON인 팔을 억지로 꺾지 마세요. 각 자세에서 팔을 안정적으로 지지한 상태로 관측 등록합니다. 아래 명령은 모터에 목표/토크/보정 값을 쓰지 않습니다.

```bash
# 예: OMX4. 실제 집기 작업 자세에서
omx_stage F pick
# 실제 놓기 작업 자세에서
omx_stage F place
```

OMX1은 A/B 각각, OMX2는 C/D 각각, OMX3은 E, OMX4는 F를 등록합니다. 서로 다른 모델의 작업 자세를 확인 없이 복제하지 마세요. 모델이 해당 단계에 머무는 실제 자세를 사용하며, 그리퍼 상태도 포함합니다. 빠른 동작 중 수동 등록하지 말고 정지 상태에서 기록합니다.

`workflow.items.<품목>.stages`에서 `tolerance`(기본 5), `samples`(기본 2)를 조정할 수 있습니다. 허용 오차를 크게 해서 영역을 겹치게 만들지 마세요. 모델이 작업 자세를 거쳤는데 관측이 누락되면 `joint_errors.json`의 stage_index/stage_total로 어느 단계가 부족한지 확인합니다. 모터 보정과 홈을 재등록해서 맞추지 않습니다.

같은 이름으로 재등록하면 해당 단계만 교체하고 순서는 유지합니다. 순서 전체를 다시 만들려면 명시적으로 지운 후 다시 관측합니다.

```bash
./omx_control --config "$OMX_CONFIG_PATH" stage-clear --item F
```

물건 이동 성공과 동작 사이클은 다릅니다. OMX1/2의 카운트는 등록 동작 단계와 홈 복귀 확인이며 실제 집기/바구니 투입을 센서로 확정한 수량은 아닙니다.

### 14-3. OMX3/4 빈 바구니 기준과 ROI

모델을 멈춘 상태에서 기존 등록 홈으로 팔을 배치합니다. 토크가 OFF라면 홈 접근 범위에서 팔을 지지하고 `omx_arm`, 지지 손을 뗀 뒤 `omx_home`으로 자세를 확인합니다. 상단 카메라 위치와 바구니 위치·조명은 실제 운용 때와 같아야 합니다.

```bash
omx_basket snapshot
# 출력: .../state/manager/omxN/basket_view.png (좌표 격자 표시)
```

이 PNG를 열어 바구니 **안쪽만** 포함하는 영역을 정합니다. 팔·그리퍼·컨베이어·외부 배경은 제외합니다. 아래는 형식 예시이며 숫자를 그대로 사용하지 않습니다.

```bash
./omx_control --config "$OMX_CONFIG_PATH" set --key unload.roi --value '[실제_x, 실제_y, 실제_가로, 실제_세로]'
# 바구니가 실제로 비었는지 직접 확인한 다음
omx_basket capture
omx_basket check
```

`capture`는 빈 바구니 여부를 사용자가 확인한 것으로 간주하고 기준 영상을 저장합니다. 상단 카메라만 열고 폴더에 작은 NPZ를 저장하며 홈/모터 보정은 변경하지 않습니다. 기본 5장의 신선하고 안정된 영상으로 비교합니다. 영상 판정용 작은 ROI만 축소하고 모델 입력 영상은 바꾸지 않습니다.

물건 0개/1개/여러 개를 넣어 `check`를 반복하세요. EMPTY / OCCUPIED / UNKNOWN 결과를 반환합니다. 같은 프레임 반복, 오래된 영상, 심한 노출 변화, 흔들림, 두 임계값 사이의 애매한 경우는 EMPTY로 완료하지 않습니다. 물건이 배경과 같거나 너무 작거나 가려지면 오판할 수 있으므로 실제 물건 전체 종류와 배치를 시험해야 합니다. 바구니·카메라가 이동하면 기준을 다시 등록하세요.

`unload.pixel_threshold`, `empty_ratio`, `occupied_ratio`, `illumination_limit`, `stability_delta`, `stable_samples`에 한글 설명이 있습니다. UNKNOWN을 없애려고 허용 범위를 무작정 넓히지 말고 조명·노출·ROI부터 확인합니다. 정확한 물품 수/컨베이어 도착 여부는 이 영상 비교만으로 보장하지 않습니다.

### 14-4. 안전한 복귀 경로 등록과 실물 시험

등록 경로는 `먼 작업 자세 → 중간 경유 자세들 → 기존 홈` 순서입니다. 원점을 새로 정하지 않습니다. 팔을 지지하고 토크 OFF 상태에서 각 자세를 순서대로 관측합니다.

```bash
# 실제 먼 작업 위치에서
omx_route fold
# 충돌을 피하는 다음 경유 자세에서, 필요한 만큼 반복
omx_route fold
# 마지막으로 기존 홈에 배치한 상태에서
omx_route fold
```

같은 이름 호출은 경유점을 **추가**합니다. 잘못 추가한 지점은 YAML의 해당 points 항목에서 확인하고 수정하세요. 수정하면 이전 경로 승인은 무효가 됩니다. 마지막 자세가 기존 홈의 정밀 도착 범위 안이면 기존 홈 값으로 기록됩니다.

홈에서 `omx_arm`으로 현재 위치 유지 토크를 확인한 뒤 손을 뗍니다. 주변과 경유 구간을 비우고 다음을 실행합니다.

```bash
omx_route_test fold
```

**실제 이동 명령입니다.** 홈에서 기록 경로를 역방향으로 따라 첫 지점까지 갔다가 다시 정방향으로 홈에 돌아옵니다. 처음부터 전체 경로를 통과하고 도착해야 approved 해시가 저장됩니다. 리더암, 원격 토크 켜기, 모터 재부팅은 사용하지 않습니다. 중단·시간초과·모드/토크/피드백 오류 시 승인하지 않습니다.

관절 공간의 선분 이동이므로 두 자세가 멀면 경유점을 더 촘촘히 추가해야 합니다. 모든 실제 물체/바구니/케이블/하중 조건의 충돌 안전을 소프트웨어가 자동 입증하지는 않습니다. 실제 사용할 환경과 하중에서 확인하세요. 기본 corridor radius=1.0, 최대 2.0이며 경로를 자동 확대하지 않습니다. 서로 멀리 떨어진 선분이 겹쳐 진입 위치를 구분할 수 없으면 거절합니다.

자동 오류 복귀는 그리퍼를 현재 위치에 유지합니다. 물건을 쥔 상태에서 임의로 놓지 않습니다. 팔만 홈에 왔고 그리퍼가 등록값과 다르면 ARM_HOME_RECOVERED로 남고 점검을 기다립니다. 등록 경로의 팔 이동을 그대로 사용하며 팔의 허용 구간을 넓히지 않습니다.

등록 홈·모델·카메라·기존 모터 보정 또는 경로가 변경되면 경로 승인은 무효입니다. 임의로 approved를 true로 편집해도 승인으로 인정되지 않습니다. 알려지지 않은 자세/모터 오류/토크 OFF에서는 복귀를 거절합니다.

```bash
# 시험된 경로 또는 기존 근거리 홈 접근 구간을 이용한 수동 복귀
omx_return_home
```

### 14-5. 등록 검사와 반복 하차 실행

```bash
omx_check
omx_workflow
omx_start --diagnostics
```

`omx_workflow`는 등록과 연결 해시를 검사합니다. 실물 성공률을 인증하지 않습니다. 명령/상태 감시는 다른 터미널에서 수행합니다.

```bash
# OMX3 또는 OMX4에서, 예상 수량 없이 빈 바구니까지 제한 반복
omx_unload
# 실제로 3개가 들어 있으면 빈 바구니 및 추정 이동 횟수도 대조
omx_unload 3
# 같은 기능을 일반 명령으로 요청 (OMX4 예)
omx_order F 3
```

FMS 명령의 items 수량도 이제 하차 컨트롤러까지 전달합니다. 모든 품목 수량 합계를 예상 하차 수량으로 사용합니다(바구니 최대 8). 기본 require_quantity_match=true에서는 예상 수량에 도달해도 바구니에 물건이 남으면 불일치로 중단하고, 먼저 비어도 추정 이동 횟수가 다르면 불일치로 중단합니다. 수량 없는 `omx_unload`는 빈 바구니 확인만 종료 기준으로 사용합니다.

순서는 `홈에서 바구니 검사 → 물건 있음 → 작업 단계·홈 복귀 한 사이클 → 정밀 홈 맞춤 → 영상 재검사 → 다음 집기`입니다. 한 사이클마다 ACT 큐/내부 상태를 초기화하고 모델·카메라는 계속 연결합니다. 홈에 잠깐 접근했다는 이유로 남은 모델 동작을 끊지 않습니다.

attempts는 시도 횟수, completed_cycles는 등록 동작 사이클, estimated_transfers는 전후 영상 변화가 있었던 사이클의 **추정** 횟수입니다. 물체가 움직이기만 했거나 여러 개가 한 번에 이동하면 정확한 개수와 다를 수 있습니다. MQTT progress에 count_is_estimate=true를 붙이며 DONE은 연속된 빈 바구니 판정 후에만 나옵니다. 하차 목적지에 정확히 놓였다는 보장은 별도 센서가 필요합니다.

기본 max_attempts=16, max_no_change=2, 한 동작 제한 runtime.cycle_timeout_s=180, 전체 job_timeout_s=900, 검사 inspect_timeout_s=12입니다. 불확실한 영상을 성공으로 처리하거나 무한 반복하지 않습니다. 이미 빈 바구니에 수량 없는 명령을 보내면 팔 동작 없이 완료할 수 있습니다.

### 14-6. 오류·강제 종료·복구 대기

사용자가 종료한 경우에는 자동 복귀 이동을 하지 않습니다. 추론이 끝난 직후에도 종료 신호를 다시 확인하여 새 모터 목표를 보내지 않습니다. 일반 오류는 모델 전송을 중단한 뒤 신선한 모터 오류/토크/모드/위치 확인을 거쳐 근거리 홈 접근 또는 시험된 경로로 복귀를 한 번 시도합니다. 실패한 주문을 성공으로 바꾸거나 다시 재생하지 않습니다.

돌아올 수 없는 자세나 오류이면 무거운 모델을 반복 로딩하지 않고 **RECOVERY_WAIT**로 들어갑니다. 모델 자원·카메라·포트를 정리한 다음 별도의 작은 MQTT 클라이언트가 ERROR/RECOVERY_WAIT와 주문 거절을 유지합니다. 모델이 없더라도 `omx_monitor`/`omx_status`에서 대기 사유를 볼 수 있습니다. 브로커 연결 자체가 없으면 로컬 기록만 남고 MQTT 전송은 재접속을 기다립니다.

모터 오류를 지우려고 강제 토크 ON, Dynamixel 재부팅, Pi OS 재부팅을 자동 실행하지 않습니다. 통신과 위치를 신뢰할 수 없으면 자동 이동하지 않습니다. 감독 프로그램도 죽는 경우는 기존 systemd 서비스가 재시작하되, 미완료 작업 기록은 보존합니다. 서비스 설치만으로 부팅 자동 시작이 켜지는 것은 아닙니다.

물품과 작업 공간을 확인하고, 필요하면 감독 프로그램을 멈춰 팔을 지지하여 알려진 홈/복귀 구간으로 배치합니다. 실행 중인 복구 대기에는 다음 명령으로 명시적으로 재점검을 요청할 수 있습니다.

```bash
omx_status
omx_resume
```

resume은 물품 상태를 확인했다는 요청입니다. 중단 주문을 보관/실패 처리하고 홈·설정을 재점검합니다. 이전 주문 ID를 자동 재실행하지 않으므로 이후에는 새 ID의 주문을 보냅니다. 카메라/설정 문제가 해결되지 않았으면 대기를 유지합니다. 기존 `omx_recover`는 감독 프로그램을 멈춘 뒤 기록을 정리하는 명령이고, 실행 중 대기에는 `omx_resume`을 사용합니다.

진단 파일은 같은 실행의 pid/session/at/monotonic을 맞춰 비교합니다. 다른 PID의 이전 ARRIVED와 현재 작업의 joint_errors를 섞어 판단하지 않습니다.

- joint_errors.json: 현재 홈 판정과 작업 단계 진행.
- last_cycle.json: 마지막 완료 당시의 초기화 전 판정 근거(추가 idle 관측으로 덮어쓰지 않음).
- unload_progress.json: 시도/사이클/추정 이동/예상 수량/빈 바구니 결과. 강제 종료 후에도 남음.
- home_motion.json: 마지막 제한 홈/경유 이동 결과.
- recovery_motion.json: HOME_RECOVERED / ARM_HOME_RECOVERED / RECOVERY_WAIT.
- recovery_wait.json: 감독 프로그램의 현재 대기 사유. 모델/카메라 초기화 로그 반복과 구분.

### 14-7. 검증 범위

`bash scripts/verify_manager.sh`와 `python scripts/repeat_manager_validation.py --runs 20`은 가상 관절·영상·프로세스·MQTT 대역으로 실행합니다. 새 테스트는 홈 스침/잘못된 단계 순서/관측 공백/연속 두 주문/여러 물건/헛집기/수량 불일치/영상 오래됨/조명 변화/알 수 없는 복귀 자세/미승인 경로/중단 신호/그리퍼 유지/왕복 경로 승인/복구 중 경량 통신을 검사합니다. 실제 모터·브로커에는 접속하지 않습니다.

이 검증은 Pi 5 4GB용 설정과 저부하 구조를 대상으로 하지만 실제 Pi 처리 속도, 학습된 ACT 동작 품질, 카메라 판정 성공률, 경로 충돌 안전은 실물 시험이 필요합니다. 모델 추론이 빨라졌다는 결과는 포함하지 않으며 기존 학습 모델과 30Hz 설정을 유지합니다.


## 15. 현재 권장: 모델 실행으로 사이클 보정·CPU 4스레드·바구니 실시간 검사

15절이 이전의 pick/place 수동 단계 등록보다 우선합니다. OMX1/2/3/4 모두 집는 위치가 여러 곳이어도 같은 방식으로 동작합니다. 이제 수동으로 집기/놓기 자세를 맞출 필요가 없습니다. 고정 시작 홈과 모터 영점은 그대로 유지하고, 모델이 실제로 돌아오는 별도의 복귀 영역을 품목별로 기록합니다.

### 15-1. 업데이트와 기존 설정에 적용

모델 실행은 Ctrl+C, 서비스 실행은 `omx_service stop`으로 멈춥니다. 기존 현장 YAML이나 홈을 템플릿으로 덮어쓰지 않습니다.

```bash
cd ~/Logistics_OMX
git pull --ff-only origin dev
source ~/.bashrc
omx_tune
```

`tune`은 CPU 스레드 4개, cycle 카운터, 바구니 검사 0.1초/3프레임/최소 0.2초 확인을 적용합니다. 기존 모델·홈·모터 보정·카메라·ROI·빈 바구니 기준은 보존합니다. 다른 명령 환경이라면 `bash scripts/install_omx_shell.sh`를 한 번 실행하고 `source ~/.bashrc`로 다시 불러옵니다.

CPU 4스레드는 모델 4개를 복제한다는 뜻이 아닙니다. PyTorch의 한 모델 연산에 intra-op 4개를 허용하고 inter-op는 가능한 경우 1개로 제한합니다. CPU 사용률 400% 또는 목표 30Hz 달성을 보장하지 않습니다. 로그의 `[CPU]`와 `omx_monitor`의 실제 스레드 수/ACT_chunk/모터 전송 Hz를 확인하세요. 방열판만 있는 Pi의 장시간 온도와 실제 처리 속도는 현장 확인이 필요합니다.

### 15-2. 손으로 자세를 맞추지 않고 모델 복귀 보정

보정은 기존 등록 홈에서 시작합니다. 팔이 기존 홈 접근 범위 안이고 토크가 켜져 있으면 `omx_home`으로 먼저 정밀 홈을 확인하세요. 멀리 뻗은 상태라면 승인한 복귀 경로 또는 기존 수동 복구 절차를 사용합니다. `omx_capture`로 현재 위치를 새 홈으로 만들지 않습니다.

각 Pi에서 해당 품목을 하나씩 보정합니다.

| 팔 | 실행할 보정 명령 |
|---|---|
| OMX1 | `omx_cycle_calibrate A`, 이후 `omx_cycle_calibrate B` |
| OMX2 | `omx_cycle_calibrate C`, 이후 `omx_cycle_calibrate D` |
| OMX3 | `omx_cycle_calibrate E` |
| OMX4 | `omx_cycle_calibrate F` |

이 명령은 모델을 실제로 움직입니다. FMS나 MQTT 주문을 보내지 않는 독립 보정 모드이며, 준비한 물품과 작업 공간을 사람이 확인하면서 수행합니다. OMX1의 기존 A/B 반대 모델 매핑은 그대로 적용합니다. 시작 전에 모터 토크와 모드를 읽어 확인하고 자동 토크 ON/영점 변경은 하지 않습니다.

한 모델당 기본 3회입니다.

1. 준비 화면에서 물건과 작업 공간을 확인하고 손을 뗀 뒤 Enter를 누르면 모델이 동작합니다.
2. 실제로 집기·놓기·복귀가 끝났을 때 Enter를 누릅니다. 헛집기/미완료/불안정 동작은 정상으로 확인하지 말고 q 또는 Ctrl+C로 취소합니다.
3. 모델 명령 전송을 중단하고 신선한 관절 피드백으로 복귀 흔들림을 기록합니다. 멀리 뻗은 자세나 아직 움직이는 자세를 홈으로 학습하지 않습니다.
4. 다음 회차를 위해 기존 고정 홈으로 제한 이동한 다음 다시 준비 화면을 표시합니다. 화면이 준비 상태가 되기 전에는 팔/물건에 손대지 않습니다.
5. 서로 다른 집기 위치를 포함하여 정상 3회를 확인하면 모델 복귀 중심·관절별 허용 폭·이동량·시간을 YAML에 저장합니다.

3회 확인 전 취소하거나 중간 오류가 발생하면 활성 보정값은 바꾸지 않습니다. 원시 자료는 `state/manager/omxN/cycle_calibration/<session>/`에 남고, 프로그램이 임의로 정상 판정을 붙이지 않습니다. 보정 창이 강제 종료되면 자식 모델도 부모 상태를 확인해 새 목표 전송을 멈추고 정리합니다. 보정 과정의 프레임/관절 기록에는 정상 주문 카운트를 올리지 않습니다.

`counting.profiles.<품목>`이 카운트용 모델 복귀 설정입니다. `home.target`은 기존 고정 시작 자세입니다. 둘이 다른 것이 정상일 수 있습니다. 예를 들어 고정 손목 54, 정상 모델 종료 손목 44가 관측되면 고정 홈을 44로 덮어쓰지 않고, 카운트용 복귀 영역을 별도로 저장합니다.

### 15-3. 카운팅 기준

정상 운전에서는 pick/place 관절 자세를 필수로 요구하지 않습니다. 다음을 모두 만족할 때 동작 사이클 1회를 기록합니다.

- 어깨/팔꿈치의 주요 관절이 복귀 영역에서 충분히 벗어나 일정 시간 작업한 이력.
- 최소 모델 명령 전송 횟수와 최소 사이클 시간 충족. 고정 홈 보정 명령은 여기에 포함하지 않음.
- 학습한 모델 복귀 영역에서 다섯 팔 관절이 안정적으로 관측됨. 그리퍼 벌림은 카운트 조건에서 제외.
- 관측 공백을 정지 시간에 합산하지 않고, 신선한 관측으로 return_hold_s 유지.

한 번 세면 다시 충분히 이탈하기 전에는 추가로 세지 않습니다. 홈 근처를 지났다는 이유로 남은 모델 동작을 미리 중단하지 않습니다. 평소 운전 중 실패한 위치를 새 홈으로 자동 학습하거나 복귀 허용 폭을 자동 확대하지 않습니다.

이 수치는 **동작 사이클**이며 실제 물품 이동 성공 수를 뜻하지 않습니다. 물건을 못 집고 한 바퀴 돌아와도 동작 사이클일 수 있습니다. OMX3/4의 실제 전체 하차 종료에는 빈 바구니 영상 확인과 수량 대조가 별도로 적용됩니다.

```bash
omx_workflow
omx_start --diagnostics
# 다른 터미널: OMX1 예
omx_order A 1
# OMX3/4에서 영상 시야·판정 검증을 마친 경우
omx_unload 1
```

`joint_errors.json`에서 `counter_mode=cycle`, `phase`, `actions`, `min_actions`, `peak_excursion`, 학습한 복귀 목표/오차를 확인합니다. stage_index/stage_total은 사용하지 않으므로 null입니다. `last_cycle.json`에는 실제 완료 시점 근거가 보존됩니다. `home_motion.json`의 목표는 고정 시작 홈이므로 카운팅용 목표와 구분하세요.

### 15-4. 바구니 딜레이 확인과 watch

기존 한 번 검사 명령은 매번 카메라를 연결하고 설정된 warmup(기본 5초)을 거친 뒤 판정합니다. 준비 시간과 이미지 연산 시간은 다릅니다. 계속 물건을 넣고 빼며 시험할 때는 연결을 유지하는 watch를 사용하세요.

```bash
# 모델을 멈추고 팔을 기존 정밀 홈에 둔 상태
omx_basket watch
```

카메라를 한 번 연 다음 상태가 바뀔 때 즉시 출력하고, 같은 상태에서는 약 1초마다 갱신합니다. 종료는 Ctrl+C이며 카메라와 포트를 닫습니다. 기준 영상은 계속 기존 파일을 사용합니다. 물건을 꺼냈다고 기준을 다시 capture하지 않습니다.

- verdict: EMPTY / OCCUPIED / UNKNOWN. 물건을 넣거나 뺀 직후에는 이전 확정값을 유지하지 않고 UNKNOWN으로 바뀝니다.
- candidate: 현재 영상의 잠정 상태. 확정값이 아닙니다.
- reason: STABILIZING / CONFIRMED / WAIT_NEW_FRAME / STALE_FRAME / LIGHTING_OR_UNCERTAIN 등.
- frame_age_ms: 판정에 사용한 프레임 나이.
- processing_ms: 작은 ROI의 비교 연산 시간.
- connection_s: 처음 카메라 연결과 준비에 걸린 시간.

기본 0.1초 간격, 서로 다른 3프레임, 최소 0.2초의 신선한 관측을 요구합니다. 이 약 0.2초는 설계상 최소 확인 창이며 실제 Pi 지연을 측정한 성능 수치는 아닙니다. 오래된/중복/시각이 맞지 않는 프레임을 확정에 재사용하지 않습니다. 큰 밝기 변화나 애매한 변화는 UNKNOWN입니다.

정상 하차 실행 중에는 카메라를 따로 열지 말고 다음 명령으로 컨트롤러의 최신 결과를 읽습니다.

```bash
omx_basket status
```

검사 중에는 최신 판정·프레임 나이·연산 시간이 나오고, 모델 동작 중에는 팔이 시야를 가릴 수 있으므로 UNKNOWN/POLICY_RUNNING입니다. 오래된 결과 파일도 UNKNOWN으로 표시합니다. 이는 장치를 열지 않는 상태 조회입니다.

영상과 타임스탬프는 같은 프레임 버퍼에서 한 쌍으로 읽습니다. 검사 중에는 관절과 필요한 상단 카메라만 읽고 손목 영상의 불필요한 추가 처리를 줄였습니다. 영상은 같은 프로세스의 최신 프레임 버퍼를 참조하고 작은 ROI만 변환합니다. 기준 영상 통계는 재사용하며, 프레임을 다른 프로세스로 직렬화하거나 복제하지 않습니다. 상태 파일은 작은 메타데이터만 상태 변화 시 즉시/안정 시 제한 주기로 기록합니다.

### 15-5. 시야와 실제 검증 범위

OMX3 사진처럼 바구니 일부가 화면 밖이면 지연을 개선해도 화면 밖 물건의 유무는 알 수 없습니다. 바구니 전체 시야와 가운데/모서리/다시 정차한 위치에서의 판정 검증 전에는 전체 자동 하차 완료 시험을 진행하지 않습니다. 모델이 학습한 카메라 각도를 임의로 바꾸는 것으로 해결하지 않습니다.

검증 기본 반복은 이제 5회입니다.

```bash
python scripts/repeat_manager_validation.py --runs 5
```

최신 검사는 네 팔의 여러 집기 위치·학습한 자연 복귀·중복 방지·손목/그리퍼 흔들림·관측 공백·잘못된 학습 자료·두 연속 하차 주문·프레임과 시각 일치·물건 제거 시 이전 판정 제거·watch 단일 연결/종료·보정 확인 중 새 목표 차단·부모 종료 감시·4스레드 설정·ACT 큐 전처리 생략의 출력 보존을 가상으로 확인합니다. 실제 Pi 모터/카메라/추론 속도는 별도 현장 시험이 필요합니다. 청크 추론 자체는 동기 방식이므로 CPU 4스레드와 큐 최적화가 추론 중 공백을 완전히 없앴다고 가정하지 않습니다.
