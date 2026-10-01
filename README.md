# OMX 물류 피킹 — Raspberry Pi / LeRobot ACT

A/B 주문을 받아 물건을 바구니로 옮기는 OMX follower 운영 코드입니다.
A 모델은 200에피소드·150,000스텝, B 모델은 100에피소드·150,000스텝 체크포인트를 사용합니다.

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

이 폴더는 기존 팀 저장소에 추가할 수 있는 구성입니다. 기존 README에는 이 문서 링크를 추가하고,
기존 `.gitignore`에는 필요한 제외 규칙을 합치세요. 기존 backend/frontend 등은 교체할 필요가 없습니다.
`robots_ws/src/omx_manipulator`는 ROS 패키지가 아니라 LeRobot 수정본 배포 자료입니다.

현재 패키지는 PC에서 확인한 소스의 스냅샷입니다. 라즈베리의 현재 환경과 동일하다는 검증은
환경 수집 및 대조 후에 완료됩니다. 소프트웨어 수준 A/B 로딩과 순차 주문 테스트는 완료했지만,
라즈베리의 실제 팔 동작 검증을 의미하지는 않습니다.
## 선정된 ACT 모델

| 물건 | 학습 에피소드 | 선정 스텝 | 모델 다운로드 |
|---|---:|---:|---|
| A | 200개 | 150,000 | [A 모델](https://huggingface.co/Jeonminjun/omx_item_a_200ep_act_150k) |
| B | 100개 | 150,000 | [B 모델](https://huggingface.co/Jeonminjun/omx_item_b_100ep_act_150k) |

두 모델은 별도로 학습했으며, 주문 품목에 따라 모델을 전환합니다.
`A 2 B 3` 입력 시 A 2개 작업 완료 후 B 모델로 전환하여 3개를 처리합니다.

모델을 다운로드할 때는 `model.safetensors`뿐 아니라
전처리·후처리 JSON과 정규화 safetensors 파일도 함께 받아야 합니다.
