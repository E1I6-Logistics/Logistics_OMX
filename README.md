# OMX 물류 피킹 — Raspberry Pi / LeRobot ACT

A/B/C/D 주문을 받아 물건을 바구니로 옮기는 OMX follower 운영 코드입니다.
OMX01은 A/B를, OMX02는 C/D를 담당하며 주문 품목에 따라 해당 ACT 모델로 전환합니다.

```text
robots_ws/src/omx_manipulator/
  upstream.json       # 원본 저장소·커밋
  overlay/src/lerobot/ # 수정한 LeRobot 소스
  runtime/            # 주문 콘솔·모델 경로·실행 스크립트
scripts/              # 가상환경 내 설치 및 실행 파일 배치
tools/diagnostics/   # 라즈베리 실제 버전 수집 (아래 폴더 참조)
doc/                  # 설치·운영·모델·버전 설명
```

- [설치](doc/raspberry_pi_setup.md)
- [주문 실행](doc/operation.md)
- [버전과 수정 파일](doc/versions.md)
- [모델 파일 준비](doc/models.md)
- [MQTT 통합 인수인계 가이드](doc/OMX_MQTT_실행_스크립트_인수인계_가이드.docx)

이 폴더는 기존 팀 저장소에 추가할 수 있는 구성입니다. 기존 README에는 이 문서 링크를 추가하고,
기존 `.gitignore`에는 필요한 제외 규칙을 합치세요. 기존 backend/frontend 등은 교체할 필요가 없습니다.
`robots_ws/src/omx_manipulator`는 ROS 패키지가 아니라 LeRobot 수정본 배포 자료입니다.

현재 패키지는 PC에서 확인한 소스의 스냅샷입니다. 라즈베리의 현재 환경과 동일하다는 검증은
환경 수집 및 대조 후에 완료됩니다. 소프트웨어 수준 A/B 및 C/D 로딩과 순차 주문 테스트는 완료했지만,
라즈베리의 실제 팔 동작 검증을 의미하지는 않습니다.

## 선정된 ACT 모델

| 물건 | 학습 에피소드 | 선정 스텝 | 모델 다운로드 |
|---|---:|---:|---|
| A | 200개 | 150,000 | [A 모델](https://huggingface.co/Jeonminjun/omx_item_a_200ep_act_150k) |
| B | 100개 | 150,000 | [B 모델](https://huggingface.co/Jeonminjun/omx_item_b_100ep_act_150k) |
| C | 160개 | 170,000 | [C 모델](https://huggingface.co/Jeonminjun/warehouse_item_c_170k) |
| D | 190개 | 170,000 | [D 모델](https://huggingface.co/Jeonminjun/warehouse_item_d_170k) |

A/B와 C/D 모델은 각각 별도로 학습했으며, 각 OMX의 주문 품목에 따라 모델을 전환합니다.
OMX01에서 `A 2 B 3`을 입력하면 A 2개 완료 후 B 모델로 전환합니다.
OMX02에서 `C 2 D 3`을 입력하면 C 2개 완료 후 D 모델로 전환합니다.

모델을 다운로드할 때는 `model.safetensors`뿐 아니라 전처리·후처리 JSON과
정규화 safetensors 파일을 포함한 `pretrained_model` 폴더 전체를 받아야 합니다.

## 현재 운영 구성

- OMX01: `run_ab_orders.sh` + `omx_order_console.sh`
- OMX02: `run_cd_orders.sh` + `omx_cd_order_console.sh`
- MQTT 주문 연동 시 OMX01은 `omx_new_order.sh`, OMX02는 `omx_cd_new_order.sh`로 주문을 전달합니다.
- MQTT 실행 파일은 ACT 실행 명령을 중복 작성하지 않고 위의 운영 스크립트를 호출하도록 구성합니다.
- 모델 가중치, 수집 데이터, 가상환경, 캐시와 백업 파일은 저장소에 포함하지 않습니다.

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
