# OMX 물류 피킹 — Raspberry Pi / LeRobot ACT

A/B/C/D/E/F 주문을 받아 물건을 바구니로 적재하거나 컨베이어로 하차하는 OMX follower 운영 코드입니다.
OMX01은 A/B, OMX02는 C/D, OMX03·OMX04는 E/F 하차 작업을 담당하며 주문 품목에 따라 해당 ACT 모델로 전환합니다.

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
환경 수집 및 대조 후에 완료됩니다. 소프트웨어 수준 모델 로딩과 순차 주문 테스트는 완료했지만,
라즈베리의 실제 팔 동작 결과는 장치 배치와 캘리브레이션에 따라 달라질 수 있습니다.

## 하드웨어 구성

| 구성 | 장치 | 역할 |
|---|---|---|
| 엣지 컴퓨터 | Raspberry Pi 5 | 주문 수신, 카메라 입력 처리, ACT 정책 추론과 로봇 제어 |
| 로봇 암 | OpenMANIPULATOR-X follower | 물건 접근, 파지, 운반과 초기 자세 복귀 |
| 모터 제어 | ROBOTIS OpenRB-150 | Dynamixel 버스 통신과 관절 명령 전달 |
| 관절 모터 | Dynamixel XL430-W250 / XL330-M288 | 어깨·팔꿈치·손목·그리퍼 6축 구동 |
| 상단 카메라 | Intel RealSense D435 또는 Logitech C920 | 작업대 전체와 물건 위치 관측 |
| 손목 카메라 | Innomaker U20CAM | 그리퍼 주변 근접 관측과 집기 보정 |
| 운반 장치 | TurtleBot + 적재 바구니 | 주문 물품의 이동과 작업대 연계 |
| 하차 장치 | 컨베이어 벨트 | E/F 물품 하차 및 다음 공정 이송 |
| 통신 | MQTT + 유·무선 네트워크 | FMS 주문 수신과 처리 상태 전달 |

## 작업 환경 및 모델별 동작

| A/B 피킹 작업 | C/D 피킹 작업 |
|---|---|
| ![A/B 모델 작업 환경](doc/images/ab_model.png) | ![C/D 모델 작업 환경](doc/images/cd_model.png) |
| 좌우 적재함에서 대상 물품을 집어 TurtleBot 바구니에 적재 | C/D 전용 작업대에서 대상 물품을 구분해 순차 적재 |

| E 하차 작업 | F 하차 작업 |
|---|---|
| ![E 모델 작업 환경](doc/images/e_model.png) | ![F 모델 작업 환경](doc/images/f_model.png) |
| TurtleBot 바구니의 E 물품을 집어 컨베이어로 이동 | F 물품 위치를 카메라로 관측하고 컨베이어로 하차 |

카메라 영상과 현재 관절 상태를 ACT 정책에 입력하면 다음 관절 목표값을 출력합니다.
한 개의 물품 처리가 끝나고 암이 초기 자세로 복귀하면 주문 완료 수량을 증가시키고 다음 물품을 처리합니다.

## 선정된 ACT 모델

| 물건 | 학습 에피소드 | 선정 스텝 | 모델 다운로드 |
|---|---:|---:|---|
| A | 200개 | 150,000 | [A 모델](https://huggingface.co/Jeonminjun/omx_item_a_200ep_act_150k) |
| B | 100개 | 150,000 | [B 모델](https://huggingface.co/Jeonminjun/omx_item_b_100ep_act_150k) |
| C | 160개 | 170,000 | [C 모델](https://huggingface.co/Jeonminjun/warehouse_item_c_170k) |
| D | 190개 | 170,000 | [D 모델](https://huggingface.co/Jeonminjun/warehouse_item_d_170k) |
| E | 132개 | 250,000 | [E 모델](https://huggingface.co/Jeonminjun/warehouse_item_e_132_act_250k) |
| F | 37개 | 230,000 | [F 모델](https://huggingface.co/Jeonminjun/warehouse_item_f_v3_5_act_230k) |

A~F 모델은 품목별로 별도 학습했으며, 각 OMX의 주문 품목에 따라 해당 모델을 불러옵니다.
OMX01에서 `A 2 B 3`을 입력하면 A 2개 완료 후 B 모델로 전환합니다.
OMX02에서 `C 2 D 3`을 입력하면 C 2개 완료 후 D 모델로 전환합니다.

모델을 다운로드할 때는 `model.safetensors`뿐 아니라 전처리·후처리 JSON과
정규화 safetensors 파일을 포함한 `pretrained_model` 폴더 전체를 받아야 합니다.

## 현재 운영 구성

- OMX01: `run_ab_orders.sh` + `omx_order_console.sh`
- OMX02: `run_cd_orders.sh` + `omx_cd_order_console.sh`
- OMX03·OMX04: E/F 하차 모델을 사용하며 현장 장치 경로와 체크포인트를 지정해 실행합니다.
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
