# OMX 물류 피킹 — Raspberry Pi / LeRobot ACT

A/B/C/D 주문을 받아 물건을 바구니로 옮기는 OMX follower 운영 코드입니다.

A 모델은 200에피소드·150,000스텝, B 모델은 100에피소드·150,000스텝 체크포인트를 사용합니다.  
C 모델은 160에피소드·170,000스텝, D 모델은 190에피소드·170,000스텝 체크포인트를 사용합니다.

OMX01은 A/B 물건을 담당하고, OMX02는 C/D 물건을 담당합니다.

```text
robots_ws/src/omx_manipulator/
  upstream.json        # 원본 저장소·커밋
  overlay/src/lerobot/ # 수정한 LeRobot 소스
  runtime/             # 주문 콘솔·모델 경로·실행 스크립트
scripts/               # 가상환경 내 설치 및 실행 파일 배치
tools/diagnostics/     # 라즈베리 실제 버전 수집
doc/                   # 설치·운영·모델·버전 설명
