# 현재 라즈베리 버전 확인

이 저장소의 진단 스크립트를 라즈베리로 복사한 뒤:

```bash
source /home/omx4/venv/.venv/bin/activate
bash tools/diagnostics/collect_pi_environment.sh
```

`~/omx_pi_environment/environment.json`과 `installed_versions.txt`가 생성됩니다.
PC로 받아 Git 커밋/소스 SHA256과 패키지 버전을 확인한 뒤 문서에 반영하세요.
설치 목록은 수집 결과이지 모든 패키지를 새 장치에 그대로 설치하라는 lockfile은 아닙니다.
보고서에는 로컬 경로와 Git 상태가 들어 있으므로 공개 전 확인하세요. 환경 변수나 인증 토큰은 수집하지 않습니다.
