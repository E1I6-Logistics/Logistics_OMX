# 기존 설치 버전 우선 정책 (2026-10-23)

OMX01은 기존 ~/venv/.venv에 다음 버전이 설치되어 정상 사용되던 것으로 기록됨:
- lerobot==0.6.2 (editable ~/il_ws/src/lerobot)
- torch==2.11.0, torchvision==0.26.0, torchcodec==0.11.1
- pyrealsense2==2.57.7.10387
- paho-mqtt==2.1.0

OMX1~4에서 현재 사용 중인 패키지/가상환경이 우선이다. 버전을 맞춘다는 이유로
새 venv를 만들거나 pip install -e 'lerobot[intelrealsense]' 를 실행하지 않는다.
이런 extra에는 LeRobot 0.6.2의 pyrealsense2<2.57 제한이 포함되어 있고,
기존 OMX01에 쓰이는 2.57.7.10387과 설치 단계에서 충돌한다.

이번 install_omx.sh는 ~/venv/.venv 를 그대로 사용하며 pip 설치를 하지 않는다.
신규 repo의 LeRobot 코드는 런타임 PYTHONPATH=~/Logistics_OMX/lerobot/src 로 먼저 선택한다.
기존 ~/il_ws/src/lerobot 와 ~/venv/.venv 는 그대로 보존한다.

필요한 프로그램이 기존 venv에 없다면 설치 스크립트는 실패한다.
누락 패키지는 Pi 별 상태를 먼저 조사하고, 실제 운영 버전을 유지하도록 개별적으로 설치한다.
OMX4 RealSense의 2.57.7.10387은 OMX01에서만 검증되었으며 OMX4의 설치 여부는 직접 확인한다.

실행 확인:
  source ~/venv/.venv/bin/activate
  python -m pip list | grep -E 'lerobot|torch|pyrealsense2|paho-mqtt'
  cd ~/Logistics_OMX
  bash scripts/install_omx.sh --robot-id omx4 --broker-ip 192.168.0.16
  source ~/.bashrc
  omx_start

설치 이전 잘못 생성된 ~/Logistics_OMX/.venv 폴더는 **사용하지 않는다**.
사용자 검증 없이 자동 삭제하지 않는다.

"requirements.txt"와 "requirements-realsense.txt"는 OMX01 패키지 버전 기록을
비교하기 위한 것이며 설치 스크립트가 자동으로 설치하지 않는다.
