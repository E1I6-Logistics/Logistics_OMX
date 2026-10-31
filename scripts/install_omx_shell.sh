#!/usr/bin/env bash
# 단축 명령만 등록합니다. 패키지 설치·모터 연결·서비스 시작을 하지 않습니다.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${OMX_PYTHON:-${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}/bin/python}"
if [[ ! -x "$PYTHON" ]]; then PYTHON=python3; fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m omx_manager.shell_setup "$@"
