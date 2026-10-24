#!/usr/bin/env python3
"""Remove failed ACT prefetch from OMX runtime; keep original sync ACT.

Safe Windows/Pi offline source transformation, anchored to e2d9dd3.
Phase 1 staged deletions are neither inspected nor touched.
No motor/MQTT usage. No implicit Git staging, commit, or push.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_HEAD = 'e2d9dd3b12ae1ea327178d29842366b8d33e8c8d'
BRANCH = 'refactor/omx-unified-runtime'
FACTORY = 'lerobot_unified/src/lerobot/rollout/inference/factory.py'
ROUTER = 'lerobot_unified/src/lerobot/rollout/order_models.py'
LAUNCHER = 'lerobot_unified/run_omx_backend.sh'
SETTINGS = 'scripts/set_omx_optimization.sh'
README = 'README.md'
SOURCE = (FACTORY, ROUTER, LAUNCHER, SETTINGS, README)
TO_DELETE = (
    'lerobot_unified/src/lerobot/rollout/inference/act_prefetch.py',
    'scripts/apply_omx_act_prefetch.py',
    'scripts/test_omx_act_prefetch.py',
    'scripts/test_omx_act_integration.py',
    'doc/OMX_ACT_PREFETCH_GUIDE_KO.md',
)
EXPECTED_SHA = {
    FACTORY: '1fa484c43611f67b3b87f92acaeec7248224d7ba',
    ROUTER: '6e3bb40338dc24b973cfc277bf8a651491325cbd',
    LAUNCHER: '2872379f3f81aef2d000729e8fa7f79df9c7ddb3',
    SETTINGS: '772ee10873cf4a2f0062b6f2af8e5d1733006917',
    README: 'b5e30fa23100627e6ae485ecaee497f3b33c10d2',
    TO_DELETE[0]: '9ceb3dcb0846b99c3a14b4e1026a9e94967f14ec',
    TO_DELETE[1]: 'ac9b0e18c021a801ea0432a48e26665f33a83f8f',
    TO_DELETE[2]: '689b2bf0af8f2b23ed413d5aa35cc7e81f27656a',
    TO_DELETE[3]: 'e412c02cfdcd069d17ba28726e8c00e84e9b8554',
    TO_DELETE[4]: '627522e9fb9d433f4f0b63291e7b997df46d9226',
}


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['git', *args], cwd=ROOT, encoding='utf-8', errors='replace',
                          capture_output=True, check=check)


def exact_one(s: str, old: str, new: str, label: str) -> str:
    count = s.count(old)
    if count != 1:
        raise ValueError(f'{label}: expected exactly one anchor, found {count}: {old[:100]!r}')
    return s.replace(old, new, 1)


def transform(sources: dict[str, str]) -> dict[str, str]:
    factory = sources[FACTORY]
    factory = exact_one(factory,
        'from .act_prefetch import create_omx_sync_engine',
        'from .sync import SyncInferenceEngine', 'factory import')
    factory = exact_one(factory,
        '        return create_omx_sync_engine(\n',
        '        return SyncInferenceEngine(\n', 'factory sync constructor')

    router = sources[ROUTER]
    router = exact_one(router,
        'from .inference.act_prefetch import create_omx_sync_engine\n',
        '', 'router import')
    router = exact_one(router,
        '        engine = create_omx_sync_engine(\n',
        '        engine = SyncInferenceEngine(\n', 'router sync constructor')

    launcher = sources[LAUNCHER]
    launcher = exact_one(launcher,
        'if [[ -f "$OMX_PREFETCH_ENV_FILE" ]]; then\n  source "$OMX_PREFETCH_ENV_FILE"\nfi\nsource "$SCRIPT_DIR/omx_runtime_env.sh"',
        'if [[ -f "$OMX_PREFETCH_ENV_FILE" ]]; then\n'
        '  # Preserve existing per-device CPU thread values in the legacy OFF config.\n'
        '  source "$OMX_PREFETCH_ENV_FILE"\n'
        'fi\n'
        '# Failed ACT prefetch removed; override any stale environment value.\n'
        'export OMX_ACT_PREFETCH=0\n'
        'source "$SCRIPT_DIR/omx_runtime_env.sh"',
        'launcher CPU env and forced OFF')

    settings = sources[SETTINGS]
    start = '  on)\n'
    stop = '  off)\n'
    if settings.count(start) != 1 or settings.count(stop) != 1:
        raise ValueError('settings on/off cases have changed')
    i = settings.index(start)
    j = settings.index(stop, i)
    settings = settings[:i] + (
        '  on)\n'
        '    echo "[ERROR] ACT prefetch was removed after unsafe physical behavior. Sync-only runtime." >&2\n'
        '    exit 2\n'
        '    ;;\n'
    ) + settings[j:]
    settings = exact_one(settings,
        '    echo "[OK] OMX optimization disabled for NEXT launcher start: $FILE"',
        '    echo "[OK] Sync ACT configuration saved for NEXT launcher start: $FILE"',
        'settings OFF message')
    settings = exact_one(settings,
        '# Local Raspberry Pi settings, no model/checkpoint modification.',
        '# Sync-only compatibility settings. Unsafe ACT prefetch cannot be enabled.',
        'settings header')
    readme = """# Logistics_OMX — OMX1~4 실운영 코드

이 저장소는 4대의 Raspberry Pi 5가 각각 LeRobot ACT 정책으로 로봇팔을 제어하는 운영 코드입니다. 현재 기준 브랜치: `refactor/omx-unified-runtime` (검증 후 배포).

- **OMX1**: A/B 피킹 (모델 매핑은 실행기에서 교차 적용됨)
- **OMX2**: C/D 피킹
- **OMX3**: E 모델 작업대 하차, Persistent Rollout
- **OMX4**: F 모델 작업대 하차, 기본 Wrist-only 정책

## 실제 호출 관계

`omx_start` → `run_omx_mqtt.sh` → `robots_ws/.../runtime/run_omx_pickup.sh` 또는 `run_omx_unload.sh` → `lerobot_unified/run_omx_backend.sh` → (1) `lerobot-rollout` (2) `omx_mqtt_client.py`.

카메라와 모터는 해당 Pi의 로컬 LeRobot이 직접 제어합니다. FMS와 OMX는 MQTT를 사용합니다. 기존 ACT 체크포인트, 가중치, 캘리브레이션과 홈 판정은 변경하지 않습니다.

### 운영 핵심

- **추론:** `SyncInferenceEngine` 고정. 문제 있었던 ACT 비동기 Prefetch는 운영 코드에서 제거되었습니다.
- **주파수:** 30Hz 설정, 새 Action Chunk를 동기 추론하는 동안 실제 제어 공백이 생길 수 있습니다.
- **배포:** 기존 OMX 장치의 설정·모델·venv를 보존하고 Git 브랜치만 순차 배포합니다.
- **완료 판정:** Home 복귀/정지 기반이며 물체를 실제로 넣었는지 증명하지 않습니다.

## 문서 및 검증

- [운영·배포·원복·환경변수](doc/OPERATIONS_CURRENT.md)
- [구조 상세](doc/ARCHITECTURE_CURRENT.md)
- 비구동 검증: `python3 scripts/verify_omx.py --runs 5` 및 `bash scripts/check_omx_release.sh`

### 모터 동작 주의

`omx_start`로 장비를 연결할 때 실제 모터 구동 가능성이 있습니다. 작업 공간·캘리브레이션·물리적 비상정지 수단을 확인하고, 신규 브랜치 첫 시험은 장비 한 대씩 진행하세요.
"""
    return {FACTORY: factory, ROUTER: router, LAUNCHER: launcher, SETTINGS: settings, README: readme}



def validate(sources: dict[str, str], deleted: bool) -> None:
    for p in [FACTORY, ROUTER]:
        ast.parse(sources[p], filename=p)
        if 'create_omx_sync_engine' in sources[p] or 'act_prefetch' in sources[p]:
            raise ValueError(f'unsafe prefetch link remains: {p}')
    f = sources[FACTORY]
    o = sources[ROUTER]
    l = sources[LAUNCHER]
    t = sources[SETTINGS]
    for token in ('from .sync import SyncInferenceEngine', 'return SyncInferenceEngine(',
                  'if isinstance(config, RTCInferenceConfig):', 'return RTCInferenceEngine('):
        if token not in f: raise ValueError(f'missing engine factory invariant: {token}')
    if 'engine = SyncInferenceEngine(' not in o or 'Only ACT models are supported' not in o:
        raise ValueError('model-switching invariant broken')
    for token in ('export OMX_ACT_PREFETCH=0', 'source "$SCRIPT_DIR/omx_runtime_env.sh"',
                  '--strategy.order_models_file=', 'ROLLOUT_SCRIPT="$SCRIPT_DIR/run_e_rollout.sh"',
                  '"$SCRIPT_DIR/omx_mqtt_client.py"'):
        if token not in l:
            raise ValueError(f'launcher invariant missing: {token}')
    if 'ACT prefetch was removed after unsafe physical behavior' not in t or 'export OMX_CPU_THREADS=4' not in t:
        raise ValueError('settings on disabled or off CPU values missing')
    if 'SyncInferenceEngine' not in sources[README] or 'doc/OPERATIONS_CURRENT.md' not in sources[README]:
        raise ValueError('current README was not installed')
    if deleted and any((ROOT / p).exists() for p in TO_DELETE):
        raise ValueError('one or more obsolete prefetch files still present')


def ensure_start() -> None:
    branch = git('branch', '--show-current').stdout.strip()
    sha = git('rev-parse', 'HEAD').stdout.strip()
    if branch != BRANCH or sha != BASE_HEAD:
        raise ValueError(f'expected {BRANCH} at {BASE_HEAD[:7]}; got {branch} at {sha[:10]}')
    # Snapshot-tracked hashes, NOT working tree hashes: Git autocrlf on Windows is supported.
    for p, expected in EXPECTED_SHA.items():
        actual = git('rev-parse', f'HEAD:{p}').stdout.strip()
        if actual != expected:
            raise ValueError(f'{p} has an unexpected Git blob ({actual}); refusing destructive changes')
        if git('diff', '--quiet', '--', p, check=False).returncode != 0:
            raise ValueError(f'{p} has unstaged changes')
        if git('diff', '--cached', '--quiet', '--', p, check=False).returncode != 0:
            raise ValueError(f'{p} has staged changes')
        if not (ROOT / p).is_file():
            raise ValueError(f'missing file: {p}')


def apply() -> None:
    ensure_start()
    originals_bytes = {p: (ROOT / p).read_bytes() for p in SOURCE}
    originals = {p: (ROOT / p).read_text(encoding='utf-8') for p in SOURCE}
    deleted_bytes = {p: (ROOT / p).read_bytes() for p in TO_DELETE}
    modified = transform(originals)
    validate(modified, deleted=False)
    modified_files: list[str] = []
    deleted_files: list[str] = []
    try:
        for p in SOURCE:
            (ROOT / p).write_text(modified[p], encoding='utf-8', newline='\n')
            modified_files.append(p)
        validate({p: (ROOT / p).read_text(encoding='utf-8') for p in SOURCE}, deleted=False)
        for p in TO_DELETE:
            (ROOT / p).unlink()
            deleted_files.append(p)
    except Exception:
        for p in modified_files:
            (ROOT / p).write_bytes(originals_bytes[p])
        for p in deleted_files:
            (ROOT / p).write_bytes(deleted_bytes[p])
        raise
    print('[PASS] Prefetch code removed (5 files); original Sync engine directly restored (2 files); README refreshed.')
    print('[PASS] Existing launcher CPU environment preserved; Prefetch forced OFF; on-command disabled.')
    print('[NEXT] python tools/refactor_phase2.py --verify')


def verify() -> None:
    source = {p: (ROOT / p).read_text(encoding='utf-8') for p in SOURCE}
    validate(source, deleted=True)
    print('[PASS] Sync ACT factory + order router; Prefetch cannot be activated from OMX launcher.')
    print('[PASS] MQTT, strategy, robot control and model paths were not modified by this script.')
    print('[NOTE] No Pi/hardware/physical-motion validation performed.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true')
    group.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    try:
        if args.verify:
            verify()
        elif args.apply:
            apply()
        else:
            ensure_start()
            sources = {p: (ROOT / p).read_text(encoding='utf-8') for p in SOURCE}
            validate(transform(sources), deleted=False)
            print('[PASS] Dry run: source hashes, exact patch anchors and unchanged safety files confirmed.')
            print('[PLAN] Edit 4 runtime files + README, delete 5 abandoned Prefetch files; leave phase-1 stage untouched.')
    except (OSError, ValueError, subprocess.CalledProcessError, SyntaxError) as exc:
        sys.exit(f'[FAIL] {exc}')


if __name__ == '__main__':
    main()
