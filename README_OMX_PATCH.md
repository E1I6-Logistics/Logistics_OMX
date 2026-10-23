# Unified source v1 notice

This ZIP is the new combined LeRobot source candidate. The most current instructions are in `doc/UNIFIED_SOURCE_OMX1_4_KO.md`. Existing instructions below may refer to the superseded `lerobot/` clone. **Use the new instructions and test before hardware execution.**

---

# Logistics_OMX 통합 실행 패치 (기존 repo에 추가)

- `run_omx_mqtt.sh` 공통 실행
- `run_omx_pickup.sh`: OMX1/2 피킹
- `run_omx_unload.sh`: OMX3/4 하차
- `scripts/install_omx.sh`: 초기 설치 및 중복 없는 `.bashrc` 등록
- `scripts/omx_shell.sh`: `omx_start`, OMX1/2 전용 `omx_reset`

기존 GitHub `main`은 변경하지 않았습니다. 패치를 PC의 Git clone 폴더에 적용한 뒤 `doc/fms_role_runtime.md`를 참고하세요.

작업대 정지 완료 조건은 빈 바구니의 물리적 검증이 아닙니다. OMX4 모델 경로는 사용자가 지정해야 합니다.

- `scripts/update_omx.sh`: Pi에 배포된 Overlay 변경사항 백업 및 갱신

## Python 의존성 관리

- `requirements.txt`: OMX1~4 공통 패키지 버전 (OMX01 환경 기준 직접 의존성)
- `requirements-realsense.txt`: OMX1/2/4의 RealSense 패키지 추가
- `requirements-pickup.txt`: 기존 파일명 호환 별칭
- `requirements/omx01-installed-20261023.txt`: 제공한 OMX01 `pip list` 전체 기록 (CUDA 포함, **배포 설치 금지**)
- `doc/requirements.md`: 설치, 비교, 검증 절차

기존 설치 스크립트는 기본적으로 LeRobot 의존성을 설치합니다. `--sync-requirements`를 명시한 경우에만 공통 요구 버전 설치를 추가로 수행합니다.


## 중요: 기존 venv 보존 수정 (2026-10-23)

기존 설치 스크립트의 신규 .venv 생성 및 RealSense extra 설치를 제거했습니다.
자세한 내용은 `doc/existing_environment.md` 참고.
