# OMX 통합 사용 설명서 — Raspberry Pi 5 4GB

기준: OMX 저장소 `dev` 브랜치. 팔마다 Pi 5 4GB 한 대를 사용합니다.
리더암 없이 기존 모터 보정 JSON과 모델의 초기 자세를 사용합니다.
이 문서의 명령은 OMX 저장소 루트에서 실행합니다. `omx1`은 해당 팔 번호로 바꾸세요.
실제 Pi·카메라·모델 추론 성능과 systemd 재시작은 현장에서 별도 확인해야 합니다.

**현재 권장 절차는 사이클 보정 15절, RGB ROI 검사 16절, 검사 전용 저노출 17절입니다.** OMX1~4 모두 pick/place 필수 조건을 제거했습니다. 수동으로 팔 자세를 맞추는 대신 모델을 실행하여 정상 복귀 영역을 기록합니다. 고정 초기 홈과 카운팅용 복귀 영역은 별개입니다. OMX3/4의 빈 바구니 기준·실물 시야 검증과 기존 복귀 경로 시험은 여전히 필요합니다. 14절의 수동 작업 단계 등록은 이전 방식 설명입니다.

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
3. 모델 명령 전송을 중단하고, 기본 최대 8초(`counting.calibration_settle_timeout_s`) 동안 연속 0.6초(`calibration_hold_s`)의 안정된 관절 관측을 기다립니다. Enter 직후 감속·잔여 흔들림은 기다리지만, 안정 허용 폭(`home.capture_max_span`)은 확대하지 않습니다. 관측 공백은 안정 시간에 합산하지 않고 다시 측정합니다. 멀리 뻗은 자세는 거절합니다.
   제한 시간에도 안정되지 않으면 어느 팔 관절의 변화 폭이 기준을 넘었는지 표시하고 모델을 정지한 채 재확인을 기다립니다. 이때 Enter는 관측만 재시도하고 모델 동작을 재개하지 않습니다. 정상 복귀 자세인지 확인할 수 없거나 계속 움직이면 q/Ctrl+C로 취소합니다. 거절된 자료는 학습에 포함하지 않습니다.
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

## 16. OMX3/4 RGB ROI 검사: 모델 원본·카메라 설정 유지

이 절은 이전 RGB 전체 변화율 검사보다 우선합니다. 뎁스를 사용하지 않으며 카메라 노출·게인·각도·모델 입력을 변경하지 않습니다. 바구니 검사에만 별도 320×240 RGB ROI와 국소 대비(CLAHE)·경계·작은 영역별 변화량을 사용합니다. 원본 카메라 배열에 그림을 그리거나 값을 변경하지 않습니다. 기준 영상의 특징과 타일 면적은 재사용합니다. 추론 중에는 이 검사를 추가 실행하지 않고 기존처럼 홈에서 하차 전후에 검사합니다.

### 16-1. 업데이트와 새 기준 등록

모델/서비스를 먼저 중지하고 다음을 실행합니다.

```bash
cd ~/Logistics_OMX
git pull --ff-only origin dev
source ~/.bashrc
omx_tune
omx_home
omx_basket snapshot
```

`omx_tune`은 OMX3/4에서 `unload.vision_method: rgb_roi`를 적용합니다. 기존 홈·학습한 모델 복귀 프로필·모터 보정·카메라 설정은 그대로 보존합니다. **기존 160×120 빈 바구니 기준은 새 검사에 사용할 수 없으므로 새로 capture해야 합니다. 홈/모델 사이클을 다시 보정할 필요는 없습니다.** 오래된 YAML에 명시된 legacy 방식은 tune 전까지 유지됩니다.

snapshot의 주황색 사각형이 ROI이며 다각형을 등록하면 분홍색 외곽선도 표시합니다. 기존 사진의 OMX3 ROI `[160,0,270,195]`는 예시로 쓰던 값이며 현재 바구니 전체 내부가 맞게 포함되는지 다시 확인해야 합니다. OMX4처럼 바구니 일부가 화면 밖이면 ROI로 그 영역을 복원할 수 없습니다. 모델이 학습한 각도는 임의로 바꾸지 않습니다.

### 16-2. 사각형·다각형 설정

설정은 각 Pi의 현장 YAML `unload`에 저장하며 모든 키에 한글 설명을 생성합니다.

```bash
# 실제 영상 좌표로 입력. 아래 값은 형식 예시이며 현장 권장 좌표가 아닙니다.
omx_basket roi '[100,80,300,250]'
# 필요하면 원본 영상 좌표로 바구니 내부의 볼록 다각형을 지정합니다.
omx_basket polygon '[[180,100],[360,130],[330,270],[120,220]]'
omx_basket snapshot
```

다각형 좌표는 ROI 사각형 안에 있어야 하며, 바구니 내부를 빠뜨린 작은 영역만 골라서는 안 됩니다. 기존 다각형을 제거하려면 `omx_basket polygon null`을 실행합니다. 기존 다각형이 새 사각형 밖으로 나가는 경우에도 먼저 해제한 뒤 새 ROI를 설정합니다. ROI/다각형 변경은 전체 시야 확인을 자동 해제하고 기준 영상 재등록을 요구합니다.

실제 정차 위치와 오차 범위에서도 **바구니 내부 전체가 검사 영역에 보이는지** 확인한 경우에만 다음을 실행합니다.

```bash
omx_basket confirm-view
# 실제 빈 바구니, 팔은 정밀 홈, 손을 치운 상태
omx_basket capture
omx_basket watch
```

`confirm-view`는 카메라를 조절하거나 시야를 자동 판별하는 기능이 아닙니다. 확인 전에도 watch로 물건 후보를 볼 수 있지만 EMPTY 확정과 자동 하차는 제한됩니다. capture는 빈 바구니임을 확인하고 실행합니다. 대비 보정값은 원본 영상과 별도로 검사 시 계산하며 바구니 물건이 있는 장면을 기준으로 자동 학습하지 않습니다.

### 16-3. 판정과 새 로그

전체 ROI 변화율에 더해, 20×20 타일의 국소 변화와 연결된 변화 덩어리를 확인합니다. 작은 변화가 물건 있음 기준에 못 미치더라도 빈 판정을 보류합니다. 하차 전후 추정 이동 횟수도 같은 작은 영역 변화 검사를 사용합니다. 정확한 물품 개수나 실제 이동 성공을 증명하는 기능은 아닙니다.

- `local_peak_ratio`: 타일 중 가장 큰 변화 비율.
- `component_pixels`: 가장 큰 연결 변화 덩어리 픽셀 수(320×240 기준).
- `edge_changed_pixels`: 기준과 달라진 경계 픽셀 수.
- `supported_edge_pixels`: 실제 RGB 픽셀 변화가 근처에서 함께 확인된 경계 픽셀 수. 경계 변화만으로 물건 있음을 확정하지 않습니다.
- `color_component_pixels`: 실제 RGB 변화의 가장 큰 연결 덩어리. 국소 물건 후보에는 이 근거도 필요합니다.
- `quality_flags`: 과노출·큰 밝기 변화·전체 시야 미확인 조건 목록. 물건 있음 후보에도 먼저 적용하여 UNKNOWN 처리합니다.
- `features_computed`: 품질 검사 통과 후 대비·경계 연산까지 실행했는지 여부. 품질 미달이면 해당 연산을 생략하고 관련 지표는 null로 표시해 CPU 낭비를 줄입니다.
- `clipped_ratio`: 기준 또는 현재 검사 영역의 RGB가 모두 250 이상인 비율.
- `illumination_delta`: 기준 대비 검사 영역의 평균 색상 변화.
- `evidence_reason`: 판정 근거. `reason`은 연속 안정 확인 상태도 함께 표시.

`ROI_OVEREXPOSED`는 물건과 바구니를 구분할 정보가 하얗게 날아간 비율이 커 EMPTY를 확정할 수 없다는 뜻입니다. `FULL_VIEW_NOT_CONFIRMED`는 전체 내부 시야 미확인, `AMBIGUOUS_LOCAL_CHANGE`는 작은 변화가 있지만 물건으로 확정하기에는 부족함, `LIGHTING_SHIFT`는 큰 밝기 변화입니다. 중복/오래된 프레임은 계속 UNKNOWN이며 기존 안정 확인 시간은 단축하지 않습니다.

과노출·시야 미확인은 OCCUPIED 후보에도 우선 적용합니다. 픽셀 변화 근거 없이 경계만 달라졌다면 `UNSUPPORTED_EDGE_CHANGE`로 보류합니다. `changed_ratio=0`은 큰 픽셀 차이가 없다는 뜻이며 낮은 문턱의 작은 물건 변화까지 없다는 뜻은 아닙니다. 경계 변화만 물건으로 확정하던 초기 RGB ROI 구현은 수정되었습니다.

전체 시야 미확인 또는 빈 기준 자체의 과노출이 확인되면 자동 운전 시작 전 검사에서 차단합니다. 이 경우 모델을 반복 로딩하지 않고 기존 복구 대기로 들어갑니다. `omx_basket watch`로 원인을 확인할 수 있습니다.

원본에서 이미 동일한 색으로 찍힌 물건, 시야 밖 물건, 검출 한계보다 작은 물건까지 보장할 수는 없습니다. 노출 설정을 유지하는 이 방식이 그 정보를 복원하지는 않습니다. `max_clipped_ratio`나 감도를 무작정 완화하여 EMPTY를 얻으려 하지 않습니다. OMX4의 잘린 시야는 이 소프트웨어 개선으로 해결되지 않습니다.

### 16-4. 현장 시험

가장 작고 밝은 물건을 포함해 빈 상태·가운데·각 모서리·여러 개·물건 제거·AMR 재정차를 시험합니다. 손을 뺀 뒤 5초 이상 유지하며 확인합니다. **물건이 있는데 EMPTY면 자동 하차를 진행하지 않습니다.** 애매한 장면이 UNKNOWN으로 남는 것은 자동 완료를 막는 동작입니다.

Pi의 추가 부하는 `processing_ms`, 프레임 나이, 실제 제어 주기로 측정합니다. 호스트 가상 시험의 속도를 Pi 성능으로 간주하지 않습니다. 모델/카메라를 실제로 구동한 검증은 별도 필요합니다.

## 17. 검사할 때만 저노출 촬영 + 같은 노출의 빈 바구니 기준 비교

이 기능은 OMX3/4 하차의 홈 검사 단계에서만 센서를 잠시 고정 노출로 전환합니다. 뎁스·추가 카메라·모델 재학습·바구니 위치 정렬을 추가하지 않습니다. `low_template`은 320×240 검사 ROI의 픽셀 차이와 작은 연결 변화만 사용하며 CLAHE/Canny는 실행하지 않습니다. 실제 Pi의 처리 시간·검출률은 현장 시험이 필요합니다.

### 17-1. 먼저 카메라 조회와 미리보기

모델/서비스/watch를 중지한 상태에서 실행합니다.

```bash
cd ~/Logistics_OMX
git pull --ff-only origin dev
source ~/.bashrc
omx_tune
omx_basket exposure
```

조회는 모터에 연결하지 않으며 검사 노출값을 센서에 적용하지 않습니다. 카메라는 원래 YAML 설정으로 연결합니다. `current`에 현재 노출·게인·자동 설정을, RealSense는 노출 범위도 표시합니다. `next_test_command`의 숫자는 현재 노출의 약 1/4을 시험 후보로 계산한 값으로, 검증된 운영값이 아닙니다. 카메라별 단위가 다르므로 다른 팔의 값을 그대로 복사하지 않습니다. OpenCV는 원시 V4L2 제어 모드만 지원하며 Windows/정규화 모드는 거절합니다. SDK·드라이버가 값을 거절하거나 다르게 적용하면 자동으로 넘어가지 않습니다.

조회 출력의 시험 명령을 실행한 다음 기존 정밀 홈에서 미리보기를 확인합니다.

```bash
# 예시입니다. 100 대신 해당 카메라 조회 출력의 시험값을 사용하세요.
omx_basket low 100
omx_home
omx_basket low-snapshot
```

`low`는 YAML의 검사 설정만 저장합니다. 원래 `hardware.cameras`·모델·홈·모터 보정은 변경하지 않습니다. 검사 모드를 적용하면서 전체 시야 확인을 해제하므로 새 저노출 영상에서도 내부 전체를 확인해야 합니다. `low-snapshot`은 실제로 센서를 잠시 전환하고 `state/manager/omxN/basket_low.png`를 저장한 뒤 원래 설정과 신선한 정상 프레임을 확인하고 종료합니다. 미리보기는 빈 기준을 등록하거나 카운트를 올리지 않습니다.

가장 작고 밝은 물건이 미리보기에서 보이는지 먼저 확인합니다. 너무 밝으면 더 낮은 시험값, 너무 어두우면 더 높은 값을 시험할 수 있지만, 모델이 켜진 상태에서 수동으로 카메라 설정을 바꾸지 않습니다. 촬영 단계에서도 구분되지 않는 물건을 이 기능이 복원하거나 검출한다고 보장하지 않습니다. OMX3처럼 바구니 일부가 화면 밖이면 저노출로도 전체 비움 판정은 할 수 없습니다.

### 17-2. 같은 저노출로 빈 기준 등록

바구니 내부 전체와 정차 오차까지 실제 시야가 확보되고 시험 노출을 정한 경우에만 진행합니다. 모터 홈·품목별 사이클 보정은 다시 하지 않습니다.

```bash
omx_basket confirm-view
# 실제 빈 바구니·정밀 홈·손을 치운 상태
omx_basket capture
omx_basket watch
```

저노출 capture는 빈 기준 이미지와 사용한 고정 노출·게인·지원되는 경우 화이트밸런스를 함께 저장합니다. OpenCV/V4L2에서는 새 기준을 만들 때 노출·게인의 지연된 실제값 차이가 1 원시 단위 이내이면서 2% 이내인 경우에만 연속 안정 구간을 확인하여 수용합니다. 예를 들어 명령 83→실제 82를 함께 기록하며, 이후 검사에서는 같은 명령 83을 보내고 저장된 실제값 82가 다시 유지되는지 엄격히 검사합니다. 매 검사마다 허용 차이를 추가하거나 기준을 자동 갱신하지 않습니다. 자동노출·자동 화이트밸런스 상태 변경, 큰 차이, 계속 흔들리는 값은 거절합니다. RealSense에는 이 V4L2 예외를 적용하지 않습니다. 이후 검사는 이 고정 제어값을 재사용합니다. 정상 노출 기준과 저노출 기준을 섞어 쓰면 거절하고, 검사 노출값을 변경하면 재등록을 요구합니다. 노출이 너무 밝거나 너무 어두운 기준은 자동 운전 시작 검사에서 거절합니다. 밝기 한도를 무작정 바꿔 우회하지 않습니다.

watch는 시험용으로 카메라 연결과 저노출 상태를 유지하며 종료할 때 복원합니다. 물건을 넣고 빼는 시험이므로 종료 시 이전 장면과 평균 밝기 일치까지는 요구하지 않고, 원래 제어 설정과 새 안정 프레임을 확인합니다. 일반 자동 검사는 물건을 움직이지 않은 상태에서 전환하므로 검사 전 원래 장면의 평균 밝기 복귀까지 함께 확인합니다. 자동노출이 켜져 있으면 수치가 장면에 따라 다시 변할 수 있어 이전 픽셀과 완전히 같은 영상이 보장되지는 않습니다.

자동 하차는 다음 순서입니다.

```text
주문 → 홈·모델 정지 → 원래 설정 기록
→ 검사 노출 적용·이전 프레임 폐기·안정 확인
→ 같은 노출 빈 기준과 비교
→ 원래 설정 복원·새 정상 프레임 확인
→ 물건 있으면 모델 1회 / 비었으면 종료 / 불명확하면 실패·대기
```

원복 전에는 모터 정책 명령을 차단합니다. 카메라를 매번 재연결하지 않으므로 실행 중 검사마다 초기 warmup 5초를 반복하지 않습니다. 다만 노출 전환·프레임 안정 대기는 추가됩니다. `processing_ms`는 이미지 비교 시간이며 `exposure_enter_s`, `exposure_restore_s`, `inspection_total_s`는 전환·복원을 포함한 별도 시간입니다. 원복 완료 시 `camera_state=NORMAL_RESTORED`, `restore_verified=true`를 보고합니다. 이때 `frame_age_ms`는 원복 대기까지 포함하여 검사 프레임이 얼마나 지났는지 표시합니다.

### 17-3. 중단·원복 실패 처리

센서 변경 전에 원래 제어값을 `state/manager/omxN/camera_exposure_pending.json`에 기록합니다. 일반 오류·Ctrl+C에서도 원복을 시도하며, 제어값이나 새 프레임 복원이 확인되지 않으면 기록을 남기고 모델 명령을 차단합니다. SIGKILL/전원 차단에서는 즉시 정리가 보장되지 않으므로 다음 실행은 이 기록을 확인해 모델을 로딩하지 않고 대기합니다. 원래 카메라와 다른 장치로 바뀌었거나 기록이 손상되면 자동으로 다른 센서를 조작하지 않습니다.

복원 미확인 메시지가 뜨면 모델/서비스를 중지한 상태에서:

```bash
omx_basket restore
```

이 명령은 기록한 원래 설정과 새 안정 프레임만 복원하며, 모터를 움직이거나 실패한 주문을 재실행하지 않습니다. 원복과 물품 확인을 마친 뒤 중단 작업은 기존 복구 절차로 대조합니다. 기록 파일을 지워 우회하지 않습니다.

저노출 검사를 끄려면 다음을 사용하고 정상 노출에서 빈 기준을 다시 등록합니다.

```bash
omx_basket low off
omx_basket capture
```

**실물 검증 필수:** 가장 작은 밝은 물건을 가운데·모서리·여러 개·제거 후·AMR 재정차 상태에서 확인합니다. 복원 뒤 모델 집기 성능도 재확인합니다. 소프트웨어 가상 시험은 실제 센서 지원이나 모델 성능 보장을 대신하지 않습니다.


## 18. Windows 노트북에서 카메라와 스냅샷 보기

노트북 브라우저에서 각 Pi의 고정 RGB 카메라와 저장된 PNG를 볼 수 있습니다.
별도의 Windows 프로그램이나 노트북 수신 서버는 필요 없습니다. 기본 허용 노트북 IPv4는 `192.168.0.19`입니다.
OMX3/4는 `top`, OMX1/2는 `top`이 없으면 `front`를 선택합니다. 실제 카메라가 향한 방향은 현장에서 확인하세요.
뷰어에서는 모터·모델·노출을 조작하지 않습니다.

### 업데이트 및 실행

각 Pi에서 기존 실행을 정상 종료한 뒤 업데이트합니다. 모델 공유 영상을 보려면 업데이트한 모델 프로세스를 한 번 다시 시작해야 합니다.

```bash
cd ~/Logistics_OMX
git pull --ff-only origin dev
source ~/.bashrc
# 뷰어 서버: 기존 카메라를 새로 열지 않음. 이 터미널은 유지
omx_view
```

다른 터미널에서는 필요에 따라 기존 `omx_start --diagnostics` 또는 `omx_basket watch`를 사용합니다.
이미 카메라를 소유한 프로세스에서 최신 프레임을 RAM 파일(`/dev/shm`, 사용자 전용 디렉터리)로 공유합니다.
브라우저가 요청할 때만 JPEG를 만들며, 기본 초당 1장·최대 가로 640픽셀·품질 70입니다.
요청이 끊기면 3초 안에 영상 변환을 멈춥니다. 유휴 게시 스레드는 작은 요청 파일만 확인하며, SD카드에 영상을 연속 저장하지 않습니다.
모델 입력 영상·해상도·노출·모터 목표에는 쓰지 않습니다. 보기용 JPEG이므로 원본 픽셀을 보존하는 무손실 전송은 아닙니다.
Pi에서의 CPU 영향과 지연은 실제 추론을 함께 돌려 측정해야 하며, 영향 0을 보장하지 않습니다.

노트북 Edge/Chrome에서 접속합니다. 아래 IP는 기존 장비 주소 배치 기준이며 다르면 각 Pi의 `hostname -I`로 확인합니다.

| 장비 | 브라우저 주소 | 기본 입력 |
|---|---|---|
| OMX1 | http://192.168.0.151:8080 | front |
| OMX2 | http://192.168.0.152:8080 | front |
| OMX3 | http://192.168.0.153:8080 | top |
| OMX4 | http://192.168.0.154:8080 | top |

4대 서버를 모두 켠 뒤 `http://192.168.0.151:8080/all`을 열면 한 페이지에서 볼 수 있습니다.
화면의 IP 입력칸을 바꾸고 연결하면 다른 주소 배치도 사용 가능합니다. 네 Pi의 웹 포트는 동일하게 설정하세요.
노트북이 Pi로 접속하므로 노트북에 수신 포트를 열 필요는 없습니다. Pi에 이미 방화벽이 있으면 해당 노트북에서 Pi TCP 8080으로의 접속이 허용되어야 합니다.

### 모델 없이 영상만 확인

```bash
# 모델·바구니 watch·보정·관련 서비스는 먼저 정상 종료
omx_view --camera-only
```

선택된 고정 RGB 카메라 하나만 연결하며, 모터 버스·ACT·MQTT에는 연결하지 않습니다.
이 모드는 기존 장치 잠금을 사용하므로 모델/보정과 동시에 시작할 수 없습니다.
Ctrl+C로 닫으면 카메라를 반납합니다. 이후 모델을 실행하려면 `omx_view`를 공유 모드로 다시 켭니다.

### 저장된 원본/저노출 사진

공유 모드 `omx_view`는 계속 켜 두어도 됩니다. 모델·watch·카메라 단독 모드는 끄고, 기존 명령을 다른 터미널에서 사용합니다.

```bash
# OMX3/4만 해당: 등록 홈에서 기존 안전 조건에 따라 촬영
omx_basket snapshot       # 원본 RGB + ROI/좌표 격자: basket_view.png
omx_basket low-snapshot   # 검사 노출 사진: basket_low.png, 노출 복원 후 저장
```

노트북 화면의 `원본 + ROI/격자`, `저노출 스냅샷` 버튼을 누르면 열립니다. 사진을 누르면 다운로드할 수 있습니다.
저장 시각을 표시하며 이 사진을 실시간 영상으로 취급하지 않습니다. 아직 사진이 없으면 없다고 표시합니다.
저노출 촬영의 홈 조건과 노출 복원 차단은 17절과 같습니다. 웹 화면 자체에서 노출을 전환하지 않습니다.
OMX3의 잘린 바구니 시야 문제는 뷰어/저노출 기능으로 해결되지 않습니다.

### 상태와 종료

- `NORMAL`: 해당 공유 프레임은 정상 카메라 설정 구간에서 가져왔습니다.
- `INSPECTION_LOW_OR_RESTORING`: 검사 저노출 또는 복원 중입니다. 원래 노출 영상이라고 표시하지 않습니다.
- 프레임이 기본 2초보다 오래되면 실시간 화면에서 제거합니다. 저장 사진에는 이 제한을 적용하지 않습니다.
- 브라우저의 실시간 일시정지/탭 숨김은 요청을 멈춥니다. 웹 서버 터미널 Ctrl+C는 보기만 종료합니다. 공유 모드에서 로봇 프로세스를 종료시키지 않습니다.
- 모델이 재시작하는 동안에는 최신 영상 없음으로 표시하며, 새 카메라 소유자가 준비되면 자동 갱신합니다.

### 설정 변경

`state/config/omxN.yaml`의 선택적 `view` 섹션으로 조정합니다. 기존 현장 YAML에 섹션이 없어도 위 기본값을 사용합니다.
기본값은 `params/omxN.yaml`에 한글 주석으로 들어 있습니다. 변경 후 뷰어와 카메라 소유 프로세스를 다시 시작해야 합니다.
예를 들어 노트북 IP가 바뀌면 모델을 정상 종료한 뒤 해당 Pi에서:

```bash
./omx_control --config "$OMX_CONFIG_PATH" set --key view.allow_ip --value 192.168.0.20
```

카메라 소유 프로세스와 뷰어는 같은 Linux 사용자로 실행하세요. 관리자 권한으로 실행할 필요 없습니다.
뷰어는 허용한 노트북 IP와 localhost만 받고 정해진 두 PNG 및 공유 JPEG만 제공합니다. 폴더 목록·YAML·작업 기록을 웹에 공개하지 않습니다.
이 서버는 로컬 장비 점검용입니다. 구현 참조: [Python HTTP 서버](https://docs.python.org/3.12/library/http.server.html), [OpenCV 메모리 영상 인코딩](https://docs.opencv.org/4.13.0/d4/da8/group__imgcodecs.html).


### 빈 바구니인데 OCCUPIED일 때: 기준·현재·차이 진단

`changed_ratio`는 기본 25 이상 차이의 비율이며, 작은 물체를 놓치지 않도록 국소 판정은 기본 6 이상 차이를 사용합니다. 따라서 changed_ratio=0이어도 국소 판정은 OCCUPIED일 수 있습니다. 이것만으로 실제 물체인지 초점·그림자·정차 위치 차이인지 확정할 수 없습니다.

모델·watch·`omx_view --camera-only`를 정상 종료하고 실제 빈 바구니 상태에서:

```bash
omx_basket diagnose
```

기존 빈 기준의 노출·게인·화이트밸런스를 재현하여 현재 ROI를 비교하고 원래 설정으로 복원한 뒤 `state/manager/omxN/basket_compare.png`를 저장합니다. 홈 조건과 기존 기준이 필요하며 모터를 이동시키지 않습니다. 출력에는 국소/전체 문턱, 변화 픽셀 수와 가장 큰 변화 영역의 좌표가 포함됩니다. 좌표는 전체 영상이 아닌 320×240 ROI 기준입니다.

다른 터미널에서 공유 모드 `omx_view`를 켜고 브라우저를 새로고침한 뒤 **기준·현재·차이** 버튼으로 봅니다. 기존 뷰어가 켜져 있었다면 코드 업데이트 후 뷰어만 다시 시작합니다. 왼쪽은 빈 기준, 가운데는 같은 설정의 현재 영상, 오른쪽의 빨강은 국소 문턱 이상 변화이고 노란 사각형은 큰 변화 영역입니다. 검사 결과가 UNKNOWN이어도 새 ROI가 확보되면 진단 사진을 저장합니다. 오래된 프레임만 있으면 저장하지 않습니다.

이 명령은 빈 기준·ROI·임계값·카운터를 자동 수정하지 않습니다. 사진을 확인하기 전에는 OCCUPIED를 EMPTY로 강제 바꾸거나 임계값을 올려 우회하지 마세요.

## 19. 위치·회전이 달라지는 바구니 정렬 검사

흰색 사각 바구니를 기존 사각 ROI 주변에서 다시 찾고, 그때의 네 모서리로 **검사 영상만 128×128로 정렬**합니다. 모델 입력, 카메라 장착 방향, 모터 홈, 기존 사이클 보정은 바꾸지 않습니다. 외부 배경을 비교하지 않고, 작은 RGB 차이가 가는 테두리 선으로만 이어지는 경우와 면적이 있는 변화를 구분합니다. 새 신경망·뎁스·추가 프로세스를 사용하지 않습니다.

기본값은 꺼짐입니다. 저노출 촬영이 정상인 OMX3/4에서 선택적으로 사용합니다. 이 기능은 물건 개수를 세지 않으며 관측한 빈 상태/물건 존재만 판정합니다. 모터 동작의 사이클 카운터는 기존 코드가 담당합니다.

### 19-1. 첫 설정: 우선 OMX4

모델·바구니 watch·카메라 단독 뷰어는 정상 종료합니다. 기존 공유 웹 뷰어도 코드 업데이트 후 다시 시작해야 새 버튼이 표시됩니다.

```bash
cd ~/Logistics_OMX
git pull --ff-only origin dev
source ~/.bashrc

# 기존 저노출 값은 유지. 고정 polygon을 설정했다면 먼저 해제
omx_basket polygon null
omx_basket align on
omx_basket pose
```

`omx_basket pose`는 등록 홈에서 저노출 사진을 확보하고 원래 카메라 설정으로 복원한 뒤, 바구니 외곽(초록)과 정렬 검사 영역(주황)을 `basket_pose.png`로 저장합니다. 모터를 이동시키지 않습니다. 기존 빈 기준은 없어도 미리볼 수 있습니다. 브라우저 확인:

```bash
omx_view
```

노트북의 각 Pi 페이지를 새로고침하고 **바구니 추적 영역**을 누릅니다. 바구니가 올바르게 선택됐고 벽 가까운 물건이 보일 영역인지 직접 확인합니다. 바구니/검색 영역이 잘리거나 후보가 여러 개이면 `BASKET_CLIPPED`, `MULTIPLE_BASKET_CANDIDATES`, `BASKET_POSE_NOT_FOUND` 등으로 거절합니다. 초록 외곽이 보인다는 것만으로 숨은 물건까지 관측된다는 뜻은 아닙니다.

바구니 내부 전체가 관측되고 실제 빈 상태이며 팔이 등록 홈에 있을 때:

```bash
omx_basket confirm-view
omx_basket capture
omx_basket watch
```

기존 고정 ROI 기준은 재사용할 수 없습니다. 새 빈 기준에는 정렬 영상, 바구니 위치, 원본 영상 크기, 저노출 실제 제어값을 함께 저장합니다. 이후 검사 때마다 이동·회전을 다시 찾고, 기준 대비 이동 거리·회전·면적 변화가 설정 범위 안인지 확인합니다. ROI/정렬 파라미터를 바꾸면 다시 기준을 등록해야 합니다. 자동 운전에 들어가기 전에 최소 물건을 중앙·네 벽·네 모서리에 놓고 빼며 시험합니다. 바구니의 정상 정차 위치가 바뀐 경우도 포함합니다.

정렬로 실제 이동이 가려지지 않도록 원본 바구니 모서리도 확정 대기 동안 설정 픽셀 범위 안에서 안정돼야 합니다. 기준 촬영에도 같은 조건을 적용합니다.

`UNKNOWN`은 빈 것으로 세지 않습니다. 자동 운전에서는 기존 제한 시간 안에 확정하지 못하면 작업을 실패/복구 대기로 처리합니다. 진단이 필요하면 watch를 종료하고 `omx_basket diagnose`를 실행합니다. **기준·현재·차이** 버튼은 정렬된 작은 영상을 나란히 보여줍니다.

### 19-2. 현재 사진에서 확인한 범위

사용자 제공 원본 사진 17장을 대상으로 OMX4 빈 사진 4장을 각각 기준으로 삼았습니다. 같은 파일을 여러 기준으로 비교한 것이므로 독립적인 현장 시행 36회가 아닙니다.

- OMX4 물건 사진 5장 × 빈 기준 4장: 20개 조합 모두 OCCUPIED.
- OMX4 빈 사진 4장 × 빈 기준 4장: 14개 조합 EMPTY, 2개 조합 UNKNOWN, OCCUPIED 오검출 0.
- OMX3 빈 사진 4장과 물건 사진 4장: 상단 잘림으로 기준 등록/확정을 보류. 물건이 없다고 판정하지 않음.
- 합성 영상에서는 약 2cm에 해당하는 14픽셀 물체의 중앙·벽 근처 검출, 이동·회전, 과도한 이동 거절, 잘림, 오래된 프레임, 기준 형식 혼용을 확인.

이는 제공 사진과 합성 영상에 대한 검증입니다. 다른 물건 재질·다른 조명·더 큰 정차 편차·실제 연속 동작 성공률을 보장하지 않습니다. Pi 5에서의 속도는 `watch`의 `processing_ms`로 확인합니다. 이 값에는 정렬과 영상 비교가 들어가고, 카메라 연결·노출 전환 시간은 별도입니다.

**OMX3는 현재 사진처럼 바구니가 화면 밖으로 잘린 상태에서 전체 바구니 비움을 확정할 수 없습니다.** 부분 시야를 감안해 진행하려면 아래 19-4의 visible_only 모드를 선택합니다. 카메라 입력을 임의 변경하지 말고, 먼저 바구니 전체가 보이는 정차/설치 조건을 현장에서 확보해야 합니다. 일부만 보고 빈 것으로 간주하는 우회는 제공하지 않습니다.

### 19-3. 파라미터와 되돌리기

모든 `unload.registration_*` 값은 현장 YAML에 저장되며 한글 주석이 붙습니다. 기본 정렬 영역은 128×128, 원본 검색 여유 60픽셀, 기준 대비 최대 중심 이동 60픽셀·회전 15도·면적 변화 ±25%입니다. 이 범위 안이어도 바구니 모양을 확인하지 못하면 UNKNOWN입니다. 검사 테두리 제외 폭은 6픽셀이며 8을 넘겨 작은 물건을 숨기는 설정은 거절합니다. RGB 문턱과 연결 면적은 기존 고정 ROI의 값과 별도입니다. 단순히 UNKNOWN을 없애려고 테두리나 문턱을 늘리지 않습니다.

```bash
omx_basket align off
# 이전 고정 ROI 방식도 빈 기준을 새로 등록해야 함
omx_basket confirm-view
omx_basket capture
```

오프라인 재현 도구는 `scripts/evaluate_basket_registration.py`입니다. `--manifest` JSON에 `roi`(장비별 사각 ROI), `samples`(id/device/path/expected/allow_unknown)를 지정하고 `--report`에 결과 경로를 줍니다. 원본 이미지는 저장소에 포함하지 않으며, 도구는 센서·모터·MQTT·정책에 연결하지 않습니다. 이미지 해시와 UNKNOWN을 포함한 결과를 남깁니다.

구현 참조: [OpenCV Otsu 문턱](https://docs.opencv.org/4.13.0/d7/d4d/tutorial_py_thresholding.html), [외곽선·다각형](https://docs.opencv.org/4.13.0/dd/d49/tutorial_py_contour_features.html), [원근 변환](https://docs.opencv.org/4.13.0/dd/d52/tutorial_js_geometric_transformations.html).


### 19-4. OMX3처럼 위쪽이 잘리는 경우: 보이는 영역만 검사

사용자가 부분 시야를 감안하고 운전하는 경우에만 선택합니다. 전체 바구니를 확인한 것으로 처리하지 않습니다. 현재 구현은 **위쪽 한 경계가 잘린 사각 바구니**만 지원하며, 두 경계 이상 잘림·모서리 추정 불확실·관측 면적 부족은 UNKNOWN입니다. 보이는 두 변의 교점으로 정렬 좌표만 추정하고, 화면 밖 영역은 실제 비교/빈 기준에서 제외합니다.

```bash
# 저노출 값은 기존 값 유지. 모델/watch/카메라 단독 뷰어 종료 후
omx_basket polygon null
omx_basket align partial
omx_basket pose
# 브라우저의 바구니 추적 영역에서 보이는 검사 범위를 확인
omx_basket confirm-visible
# 실제 빈 상태에서
omx_basket capture
omx_basket watch
```

`confirm-view` 대신 `confirm-visible`을 사용합니다. 부분 시야 모드에서는 `EMPTY`가 **보이는 영역이 기준과 일치한다**는 뜻입니다. 바구니 전체나 숨은 물건까지 확인했다는 뜻이 아닙니다. 로그에 `view_scope=visible_only`, `full_basket_verified=false`, 현재 관측 비율 `visible_ratio`가 나옵니다. 기준과 현재 영상에서 공통으로 보이는 픽셀만 비교하며, 새 기준에는 그 가시 영역 마스크도 저장합니다.

자동 하차에서 부분 시야 비움으로 종료할 때 status는 `reason=visible_empty_confirmed`, `basket_empty_verified=false`, `visible_region_empty_verified=true`, `inspection_scope=visible_only`입니다. MQTT result의 success는 이 선택된 검사 범위 내 작업 완료를 뜻하며, 메시지는 `Visible basket region empty; unseen region unverified`를 포함하고 같은 범위 플래그를 보냅니다. 확인한 현재 FMS 코드는 success=True이면 다음 단계로 진행하며 추가 범위 필드로 분기하지 않습니다. 따라서 이 모드는 보이는 영역 기준으로 종료해도 되는 운전 조건에서 사용합니다. 전체 비움 확정이 필요한 관제 로직은 이 결과를 전체 확인으로 해석하면 안 됩니다. 예정 수량이 있는 주문은 기존 수량 대조 조건도 계속 적용합니다.

제공한 사진 재생에서 OMX3 물건 사진 4장 × 빈 기준 4장인 16개 조합은 모두 OCCUPIED였습니다. 빈 사진 16개 조합은 8개 EMPTY(보이는 영역), 8개 UNKNOWN, OCCUPIED 오검출 0이었습니다. OMX4 전체 시야 결과는 앞 절과 같습니다. UNKNOWN을 지우려고 보이지 않는 영역을 빈 것으로 채우거나 기준을 자동 갱신하지 않습니다. 실제 Pi 연속 동작·다른 조명/물건은 현장에서 확인해야 합니다.

부분 시야의 기본 최소 관측 비율은 65%, 모서리 추정 한도는 ROI 크기의 35%입니다. 보이는 영역의 RGB 중앙값 차이는 최대 20까지 보정하고, 더 크면 UNKNOWN입니다. 부분 정렬의 모서리/초점 차이가 물건으로 확정되지 않도록 물건 확정 연결 면적은 기본 64픽셀, 약한 변화 보류는 32픽셀입니다. 모두 `unload.registration_*`에 한글 주석과 함께 저장됩니다. 모터 홈·모델 입력·모델 가중치는 이 설정으로 바뀌지 않습니다.
