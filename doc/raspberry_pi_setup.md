# Raspberry Pi 설치

## 1. 시스템 준비

기존 운영 라즈베리에서는 먼저 환경 보고서를 수집하세요. 아래 시스템 설치는 새 장치에 필요한 경우만 수행합니다.
Ubuntu 24.04 aarch64 / Python 3.12 환경을 기준으로 작성했습니다.

```bash
sudo apt update
sudo apt install git python3-venv python3-dev build-essential cmake pkg-config libusb-1.0-0-dev ffmpeg v4l-utils
sudo usermod -aG dialout,video "$USER"
```

그룹 변경 후 로그아웃하고 다시 접속합니다. 다른 사용자의 가상환경이나 시스템 Python 패키지를 변경하지 않습니다.

## 2. 가상환경

새 장치에서:

```bash
python3 -m venv ~/venv/.venv
source ~/venv/.venv/bin/activate
```

기존 장치에서는 생성 없이 `source ~/venv/.venv/bin/activate`만 실행합니다.

## 3. 소스 및 수정본 설치

팀 저장소를 다운로드하고 저장소 루트에서 실행합니다.

```bash
bash scripts/setup_omx_lerobot.sh
```

원본 커밋을 고정해 별도 `~/il_ws/src/lerobot_omx_release`에 설치합니다.
기존 `~/il_ws/src/lerobot`을 덮어쓰지 않습니다. 설치 파일이 이미 있으면 중단합니다.
이 스크립트의 Python 패키지 설치는 현재 활성 가상환경에만 적용됩니다.
운영 venv를 보존하려면 새 venv를 먼저 생성해 활성화하세요.

설치 대상은 LeRobot 기본 의존성에 `[dynamixel,intelrealsense]` extra를 더한 것입니다.
PyTorch/torchvision, OpenCV, NumPy, safetensors, dynamixel-sdk, pyserial,
pyrealsense2 등이 설치됩니다. CPU 추론용이며 Raspberry Pi에 CUDA를 설치하지 않습니다.
`uv sync`의 저장소 CUDA 인덱스 설정을 그대로 적용하지 않도록 여기서는 활성 venv의 pip를 사용합니다.

ARM용 pyrealsense2 wheel이 없는 조합에서는 설치가 실패할 수 있습니다.
이 경우 실제 동작하는 라즈베리의 환경 보고서와 Intel librealsense의 해당 버전 설치 방법을 확인하세요.
RealSense USB 권한용 udev rules 및 소스 빌드 필요 여부도 장치에서 확인해야 합니다.
본 패키지는 해당 장치에서 검증되지 않은 SDK 빌드를 자동 실행하지 않습니다.

## 4. 하드웨어 및 모델

```bash
ls -l /dev/serial/by-id/
ls -l /dev/v4l/by-id/
lerobot-find-cameras realsense
```

`run_ab_orders.sh`의 OpenRB 경로, RealSense 일련번호, 손목 카메라 경로를
실제 장치에 맞추세요. 샘플에는 현재 사용자 장치 식별자가 들어 있습니다.
로봇별 캘리브레이션을 준비해야 하며 다른 로봇에 기존 보정값을 무조건 복사하지 않습니다.
체크포인트 전체 폴더는 [모델 준비](models.md)를 따릅니다.

## 5. 설치 검증

```bash
python -c "import lerobot; print(lerobot.__file__)"
python -c "import torch, cv2, pyrealsense2, dynamixel_sdk; print(torch.__version__)"
python -m pip check
```

카메라/모터 연결, 추론 속도, A/B 전환은 실제 장치에서 별도 검증합니다.
30Hz는 목표 제어 주기이며 CPU 추론이 30Hz를 항상 달성한다는 뜻은 아닙니다.
