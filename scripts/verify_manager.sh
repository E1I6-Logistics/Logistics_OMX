#!/usr/bin/env bash
# Hardware-free checks only. Scratch files remain in this checkout.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$ROOT/tests/manager_tmp/system"
export TMPDIR="$ROOT/tests/manager_tmp/system"
PYTHON="${OMX_PYTHON:-python3}"
"$PYTHON" -m pytest tests/test_manager.py tests/test_follower_bus_lifecycle.py tests/test_home_motion.py tests/test_shell_shortcuts.py tests/test_diagnostics.py tests/test_service.py tests/test_workstation_protocol.py tests/test_unload_client_protocol.py -q -p no:cacheprovider --basetemp=tests/manager_tmp/pytest
"$PYTHON" scripts/test_omx_mqtt_faults.py --runs 10
"$PYTHON" scripts/verify_omx.py --runs 5
