# OMX1~4 Python requirements 관리

2026-10-23 OMX01 `pip list` 제공 기록을 기준으로 관리합니다. **다른 장비의 실측 패키지 목록은 아직 제공되지 않았습니다.**

## 구성

| 파일 | 목적 |
|---|---|
| `requirements.txt` | OMX1~4 공통 직접 의존성 버전 (`torch`, `torchvision`, MQTT, OpenCV, Dynamixel 등) |
| `requirements-realsense.txt` | OMX1/2/4 RealSense 모듈 + 공통 requirements
| `requirements-pickup.txt` | OMX1/2용 기존 파일명 호환 별칭 |
| `requirements/omx01-installed-20261023.txt` | OMX01의 `pip list` 전체 스냅샷: CUDA/NVIDIA/transitive 포함. 비교용이며 **다른 Pi에 직접 설치하지 않음** |

현재 OMX01 LeRobot은 `0.6.2`가 **editable** 모드로 `/home/omx01/il_ws/src/lerobot`에 설치되어 있습니다. `requirements.txt`에 PyPI의 일반 `lerobot==0.6.2`를 넣지 않았습니다. 저장소의 수정 Overlay를 적용한 `~/Logistics_OMX/lerobot`를 `pip install -e` 방식으로 설치해야 합니다.

## 기존 OMX01 환경 보존

현재 정상 동작 중인 가상환경에 아래 명령을 곧바로 사용하면 PyTorch를 포함한 패키지의 재설치, 의존성 충돌, 큰 다운로드가 발생할 수 있습니다. 먼저 현재 버전을 비교하세요.

```bash
cd ~/Logistics_OMX
source .venv/bin/activate
python -m pip list --format=freeze > state/pip_installed_now.txt
python -m pip check
```

새 설치에서 스크립트가 LeRobot과 필수 패키지를 설치할 때는 기본적으로 기존 로직을 사용합니다. **버전을 강제로 동기화하는 것은 명시적 옵션을 사용한 경우에만 수행**합니다.

```bash
# 배포할 장비에서 새 설치 시 (예: OMX01)
bash scripts/install_omx.sh --robot-id omx1 --broker-ip 192.168.0.16
# 버전 동기화가 반드시 필요하고, 현재 장비 PyTorch wheel/디스크 공간을 검토한 경우만:
# bash scripts/install_omx.sh --robot-id omx1 --broker-ip 192.168.0.16 --sync-requirements
```

수동 설치를 선택한 경우:

```bash
cd ~/Logistics_OMX
source .venv/bin/activate
# OMX3 (OpenCV C920)
python -m pip install -r requirements.txt
# OMX1/2 및 OMX4 (Intel RealSense)
python -m pip install -r requirements-realsense.txt
python -m pip check
```

이 명령은 실제 설치를 변경합니다. 이미 실기 운영 중인 장비에서는 백업과 신규 venv 비교를 우선하세요.

## 중요한 호환성 주의

- OMX01에 `cuda-*`, `nvidia-*`, `triton`이 설치돼 있지만 **CUDA GPU 가속이 동작한다는 증거는 아닙니다.** Raspberry Pi에서는 GPU 가속을 가정하면 안 됩니다.
- `torch==2.11.0`과 `torchvision==0.26.0`은 OMX01에서 관찰된 버전이지 모든 장비에 호환되는 wheel이라는 증거가 아닙니다. 운영체제/아키텍처/Python 버전을 확인한 후 설치합니다.
- `pyrealsense2==2.57.7.10387`도 OMX01 기준이며 OMX2/OMX4에서도 동일 wheel이 가능한지 검증이 필요합니다.
- 공통 런타임 최적화 파라미터(스레드 수)와 Python 의존성 버전은 별개입니다. `OMX_CPU_THREADS`를 변경해도 패키지 버전이 바뀌지 않습니다.
- 실제 RPM/스레드/FPS/CPU 사용률은 측정해야 하며 pip 목록만으로 성능을 판단할 수 없습니다.

## 장비별 환경 정보 수집

```bash
uname -m
python --version
python -c 'import torch; print("torch",torch.__version__,"cuda",torch.cuda.is_available(),"threads",torch.get_num_threads())'
python -m pip list --format=freeze
```

실기에서 동작하는 OMX2/3/4의 목록을 수집해 OMX01과 비교한 다음 버전을 통일하거나 역할별 예외를 문서화하세요. OMX4 메인 카메라는 RealSense이며, 손목 카메라 종류와 학습 정책의 이미지 키는 실제 장치/모델과 대조해야 합니다.
