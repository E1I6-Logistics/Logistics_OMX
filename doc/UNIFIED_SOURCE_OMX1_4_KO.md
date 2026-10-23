# OMX1~4 단일 LeRobot 통합 소스 (후보 v1)

## 소스 근거
- 2026-10-23 업로드 OMX1/2/3/4 `~/il_ws/src/lerobot` 스냅샷 기반.
- 공통 소스: `lerobot_unified/src/lerobot`; OMX3에서 피킹, 바구니 영상 변화, Home 복귀 및 정지 판정 코드를 가진 기본본을 선택.
- OMX4 `omx_f_sh`의 wrist-only 모델, 250k 체크포인트, 전용 캘리브레이션 ID를 보존.
- **병합 후보**: 모터/카메라 및 정책의 실제 회귀 검증은 아직 수행하지 않음.
- OMX4에만 있던 환경/processor/eval 유틸리티 6개는 해당 의존 파일을 함께 통합. OMX2에만 있던 `src/lerobot/rollout/base.py`는 다른 세 장비의 현재 코드에서 사용되지 않은 것으로 보여 통합 기본본에서 제외(실행 검증 필요).

## 변경 내용
- `lerobot_unified/src/lerobot`을 한 번만 Git 추적하며 4대 공통 사용.
- `~/venv/.venv` 기존 패키지 유지, **pip 설치/업그레이드 없음**.
- `lerobot_unified/outputs` → `~/il_ws/src/lerobot/outputs` symlink (초기 설치 시), 모델 복사 없음.
- OMX1/2 각 장비의 주문 모델 JSON은 `$HOME/.config/logistics_omx/`로 복사 후 독립 설정.
- OMX3는 E top(C920)+wrist, OMX4는 현재 F wrist-only ACT 250k 기본.
- F 향후 2개 카메라: `OMX4_CAMERA_MODE=top_wrist`, `OMX4_TOP_CAMERA_TYPE=opencv|intelrealsense` 및 새 모델 경로 지정.
- LeRobot `--robot.id=omx_f_test_follower_arm`, MQTT `--robot-id=omx4`는 별개.

## 적용 순서
1. Windows Git 저장소 루트에 ZIP 해제, 변경 목록 확인, Git push.
2. 각 Pi `cd ~/Logistics_OMX && git pull --ff-only` (한 대씩).
3. 모든 로봇 관련 프로세스 종료 후 `bash scripts/install_omx.sh --robot-id omxN --broker-ip 192.168.0.16`.
4. `source ~/.bashrc; bash scripts/preflight_omx.sh` (모터 연결 없음).
5. 모델 파일/USB/캘리브레이션 확인 후 별도 실기 테스트. 회귀검증 전 `omx_start`로 FMS 작업 투입 금지.
6. 옛 `~/il_ws/src/lerobot`은 삭제하지 않았으므로 기존 방식으로 되돌릴 수 있음.

## OMX4 임시/향후 카메라 설정
현재 `~/.config/logistics_omx/profile.env`:
```
OMX4_CAMERA_MODE=wrist_only
OMX4_POLICY_PATH=outputs/train/warehouse_item_f_129_merged_wrist_only_act_300k/checkpoints/250000/pretrained_model
OMX4_LEROBOT_ROBOT_ID=omx_f_test_follower_arm
```
향후 모델 교체 시 (**별도로 학습한 2카메라 체크포인트 필요**):
```
OMX4_CAMERA_MODE=top_wrist
OMX4_TOP_CAMERA_TYPE=opencv
OMX4_TOP_CAMERA=/dev/v4l/by-id/<camera>-video-index0
OMX4_POLICY_PATH=outputs/train/<future_model>/checkpoints/<step>/pretrained_model
```
RealSense이면 `OMX4_TOP_CAMERA_TYPE=intelrealsense`, `OMX4_TOP_CAMERA_SERIAL=<serial>`.
모델에 저장된 이미지 키와 런타임 `top`,`wrist`가 일치해야 함. 기존 profile.env는 덮어쓰지 않음.

## 물리적 완료 판정 한계
피킹 Home 귀환 카운트 및 하차 15초 정지는 **물품 이송의 성공 검증이 아님**.
기존 MQTT는 이 이벤트 마커로 `success=True`를 보낼 수 있으므로 FMS가 AMR을 조기 복귀시키지 않도록 실기 검증 전 MQTT 명령 투입 금지.
