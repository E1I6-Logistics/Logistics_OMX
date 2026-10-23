# OMX1~4 공통 ACT 선행 추론 (opt-in)

적용 기준: `fix/omx-safety-stability`의 `8b87889` 이후, 기존 성능 계측 패치가 있어도 유지. **기본값은 OFF**이므로 Git Pull만으로 로봇 동작이 변경되지 않는다.

## 1. 목적 및 작동 원리

- 기존 ACT 체크포인트, 모델 설정 JSON, 카메라, Home 판정, FMS/MQTT, `send_action()` 경로를 변경하지 않는다.
- 동기 추론의 Chunk 생성 시 약 1.9초간 정지하는 현상을 완화하기 위해, **ACT Chunk 추론만 별도 Python 스레드**에서 수행한다.
- 기본 `n_action_steps=100`인 ACT 모델은 현재 Chunk에서 **70 Action이 남았을 때** 다음 Chunk 계산을 시작한다. 작업 시퀀스는 기존 정책 출력 순서대로 유지한다.
- Worker는 모터 명령을 직접 보내지 않는다. 기존 제어 루프만 `send_action()`을 호출하므로 기존 OMX 오류 플래그 검사를 통과해야 한다.
- 모델 추론 중 CPU가 포화될 수 있어 OMX Pi5 4코어 중 3코어를 추론용으로 사용해 볼 수 있도록 별도 설정을 제공한다. 3코어가 반드시 4코어보다 빠르다는 의미가 아니다.
- 준비된 Chunk가 없으면 기존 Action을 반복하지 않고 그 틱의 모터 명령을 **보내지 않는다**. 이 소프트웨어 동작은 모터 물리 정지를 의미하지 않는다.
- 오류 / 모델 리셋 / 작업 변경 시 이전 Chunk를 폐기한다. Worker 타임아웃은 기본 10초다.
- **ACT temporal ensembling, ACT 외 모델, `n_action_steps < 4`, 텍스트 생성 모델은 기존 Sync로 자동 폴백**한다. 단, OMX1/2 `OrderModelRouter`는 여전히 기존 코드처럼 ACT 모델만 허용하며, 다른 모델 아키텍처로 변경하려면 모델 라우터도 별도 수정해야 한다.

## 2. Windows: 설치 전 검증 및 Git 적용

1. ZIP을 `Logistics_OMX` 저장소 최상위에 **폴더 구조 그대로** 압축 해제한다.
2. 이미 실행 중인 OMX 로봇은 그대로 둔 채, Windows에서 소스만 준비한다.

```powershell
cd "C:\Users\dbdna\OneDrive\바탕 화면\Team_Project\Logistics_FMS\omx_ws\Logistics_OMX"
python .\scripts\apply_omx_act_prefetch.py --check
python .\scripts\test_omx_act_integration.py --runs 10
python .\scripts\apply_omx_act_prefetch.py
python -m py_compile .\lerobot_unified\src\lerobot\rollout\inference\act_prefetch.py .\lerobot_unified\src\lerobot\rollout\inference\factory.py .\lerobot_unified\src\lerobot\rollout\order_models.py
python .\scripts\verify_omx.py --runs 5
git diff --check
git diff --stat
```

`test_omx_act_prefetch.py`는 PyTorch가 필요하므로 Windows에 PyTorch가 없다면 설치하지 않고 Pi5의 기존 LeRobot 가상환경에서 실행한다.

검사를 통과하고 Diff가 의도대로면 파일을 명시해서 Commit한다. 스크립트가 생성하는 `.bak_act_prefetch` 파일은 커밋하지 않는다.

```powershell
git add lerobot_unified/run_omx_backend.sh lerobot_unified/src/lerobot/rollout/inference/factory.py lerobot_unified/src/lerobot/rollout/order_models.py lerobot_unified/src/lerobot/rollout/inference/act_prefetch.py scripts/apply_omx_act_prefetch.py scripts/test_omx_act_prefetch.py scripts/test_omx_act_integration.py scripts/set_omx_optimization.sh doc/OMX_ACT_PREFETCH_GUIDE_KO.md
git diff --cached --check
git commit -m "perf: OMX1~4 공통 ACT 선행 추론 옵션 추가"
git push origin fix/omx-safety-stability
```

## 3. Raspberry Pi: 안전하게 배포

각 OMX에서 실행 중인 주문을 끝내고 로봇팔이 정지한 상태에서 런처를 종료한다. `Ctrl+C`만으로 토크 해제나 물리적인 비상정지가 보장되지는 않는다.

```bash
cd ~/Logistics_OMX
git pull --ff-only origin fix/omx-safety-stability
git log -1 --oneline
bash -n lerobot_unified/run_omx_backend.sh
python3 scripts/test_omx_act_integration.py --runs 10
source ~/venv/.venv/bin/activate
python3 scripts/test_omx_act_prefetch.py --runs 10
python3 scripts/verify_omx.py --runs 5
bash scripts/set_omx_optimization.sh status
```

**테스트 실행은 실제 로봇팔을 움직이지 않음**. OMX1~4에서 코드 확인이 끝나면 OMX1만 먼저 활성화한다.

```bash
cd ~/Logistics_OMX
bash scripts/set_omx_optimization.sh on
bash scripts/set_omx_optimization.sh status
```

`~/.config/omx/act_prefetch.env`에 설정이 저장된다. 그 후 안전한 Home 상태와 비상정지 수단을 확보하고 OMX1을 기존 런처로 실행한다.

```bash
cd ~/Logistics_OMX/lerobot_unified
export OMX_INVENTORY_STATE_PATH="$HOME/Logistics_OMX/state/omx_inventory.json"
bash run_omx_backend.sh --robot-id omx1 --broker-ip 192.168.0.16
```

**중요:** 재고 상태 파일 경로를 Rollout과 MQTT 양쪽에 동일하게 전달해야 한다. 이 패치는 경로 기본값 불일치 문제를 별도로 고치지 않는다. 시작 전 환경변수를 명시하거나, 기존 `profile.env`의 안전한 방식으로 공통 경로를 설정한다.

OMX1 실제로 소량 주문 하나만 시험한 뒤 기존 버전과 비교한다. 추론 스레드 로그 `[ACT-PREFETCH] ready ... in ... ms`, 기존 `Cadence (OMX performance window)` 로그의 평균 제어 Hz/최악 지연을 비교한다. **A/B 피킹 정확도는 이번 최적화의 범위가 아니다.**

OMX1에 문제가 없을 때만 OMX2, OMX3, OMX4 순서대로 각 장비의 `on`을 설정하고 기존 런처를 재시작한다. OMX3/4의 모델 로딩·카메라 구성은 기존 설정을 사용한다.

## 4. 조정값

| 변수 | on 기본값 | 설명 |
|---|---|---|
| `OMX_ACT_PREFETCH` | `1` | 0은 기존 Sync, 1은 ACT만 선행 계산 |
| `OMX_ACT_PREFETCH_STEPS` | `70` | 현재 Chunk의 남은 Action 기준 선행 요청. 모델의 `n_action_steps - 1`을 초과하면 자동 제한 |
| `OMX_ACT_PREFETCH_MAX_AGE_S` | `4.0` | 아직 시작하지 않은 선행 Chunk의 관측 시각 유효기간 |
| `OMX_ACT_PREFETCH_STALL_S` | `10.0` | 모델 추론 대기 상한. 초과 시 예외로 제어 루프 실패 경로 진입 |
| `OMX_CPU_THREADS` 및 OMP/MKL/OPENBLAS/NUMEXPR | `3` | Pi5 CPU 예약 실험값. 기존 벤치마크의 4스레드보다 느릴 수도 있음 |

`~/.config/omx/act_prefetch.env`를 수정하려면 작업 중이 아닌 상태에서 바꾸고 런처를 재시작한다. 권장 최초 비교: `3`스레드 vs `4`스레드를 같은 주문으로 한 번씩, **추론 지연뿐 아니라 실제 제어 Hz·최악 지연**으로 판정.

## 5. 즉시 원복

작업 중 오류가 발견되면 물리적인 비상정지 수단으로 안전을 확보하고 현재 주문을 중단한다. 움직임이 완전히 멈춘 것을 확인한 뒤:

```bash
cd ~/Logistics_OMX
bash scripts/set_omx_optimization.sh off
bash scripts/set_omx_optimization.sh status
```

다음 안전한 재시작부터 원래 Sync 추론과 CPU 4스레드로 돌아간다. Git Reset이나 모델 파일 삭제는 필요 없다.

## 6. 이미 확인된 제한

- 사전 검증: 실제 구현의 PyTorch 텐서·스레드 동작 모의 테스트 **16항목 × 10회 = 160건**, 동일한 프로젝트 파일을 대상으로 한 3위치 통합/중복적용 검사 **10회**, 로컬 옵션 on/off 스크립트 **10회**. 실제 MQTT·Pi5 물리 기구·USB 모터 실험은 포함되지 않는다.
- 평균 루프 Hz 개선은 아직 **예측**, 실측 결과가 아님. Pi의 실제 CPU 경쟁, 카메라 지연에 따라 성능 향상이 없을 수도 있다.
- **선행 관측값이 최대 수 초 오래될 수 있다.** 새 Chunk가 만들어지는 시점의 최신 관측값을 쓰는 기존 Sync와 달라서 모션 정확도 및 충돌 위험이 변할 수 있다. 반드시 감독하에 단계적으로 활성화한다.
- 과거에 전송된 서보 명령은 소프트웨어 큐 폐기만으로 취소할 수 없다.
