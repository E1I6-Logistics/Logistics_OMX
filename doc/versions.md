# 사용 버전과 확인 범위

| 항목 | 값 | 확인 근거 |
|---|---|---|
| LeRobot 표시 버전 | 0.6.2 | PC pyproject.toml |
| 원본 Git | https://github.com/huggingface/lerobot.git | PC git remote |
| 원본 커밋 | `89236ea0f4f81a81ca566081e20dd1ff5f823cbe` | PC git rev-parse HEAD |
| Python 요구사항 | >=3.12 | 해당 커밋 pyproject.toml |
| 라즈베리 OS | Ubuntu 24.04.4 LTS / aarch64 | 2026-09-29 사용자 SSH 출력; 현재 재확인 필요 |
| 라즈베리 venv | `/home/omx4/venv/.venv` | 사용자 실행 기록 |
| 라즈베리 소스 | `/home/omx4/il_ws/src/lerobot` | 사용자 실행 기록 |
| 라즈베리 현재 Git 및 패키지 | 미확인 | diagnostics로 수집 필요 |

과거 라즈베리에서 `30074f7f` 커밋이 보고됐지만 이후 PC 코드를 복사했으므로
그 커밋만으로 실행 중인 소스를 식별할 수 없습니다. Git 커밋 + 수정 파일 SHA256 + pip 목록을 함께 기록하세요.
단순히 `pip install lerobot`으로 설치하면 이 프로젝트 수정사항이 포함되지 않습니다.

## 포함된 수정 소스

- `src/lerobot/cameras/opencv/camera_opencv.py`
- `src/lerobot/cameras/opencv/configuration_opencv.py`
- `src/lerobot/policies/act/modeling_act.py`
- `src/lerobot/processor/normalize_processor.py`
- `src/lerobot/rollout/configs.py`
- `src/lerobot/rollout/strategies/base.py`
- `src/lerobot/scripts/lerobot_record.py`
- `src/lerobot/rollout/order_models.py`

OpenCV processing_fps, ACT/정규화 호환 수정, 수집 스크립트 수정,
초기 자세 복귀 카운트, 주문별 ACT 모델 전환이 포함됩니다.
`runtime`에는 단일/복합 주문 콘솔과 A/B 체크포인트 경로 설정이 들어 있습니다.

라즈베리의 정확한 torch, torchvision, pyrealsense2 버전은 추정해서 고정하지 않았습니다.
PC CUDA 환경의 pip freeze를 ARM CPU 라즈베리의 설치 목록으로 사용하지 마세요.
