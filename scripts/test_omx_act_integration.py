#!/usr/bin/env python3
"""10x fixture integration: patch real checked-out source, not fabricated snippets."""
from __future__ import annotations
import argparse
import os
import ast
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from apply_omx_act_prefetch import PY_CHANGES, SH_FILE, patches_for


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=10)
    args = parser.parse_args()
    assert 1 <= args.runs <= 100
    for i in range(args.runs):
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d)
            for name in [*PY_CHANGES, SH_FILE]:
                (dst / name).parent.mkdir(parents=True, exist_ok=True)
                source = ROOT / name
                backup = source.with_name(source.name + '.bak_act_prefetch')
                if backup.exists():
                    source = backup
                shutil.copyfile(source, dst / name)
                if name in PY_CHANGES:
                    # When tests run after Git Pull, the committed source is
                    # already patched and there may be no local backups.
                    src = (dst / name).read_text(encoding='utf-8')
                    for old_anchor, new_anchor in reversed(PY_CHANGES[name]):
                        if old_anchor not in src and new_anchor in src:
                            assert src.count(new_anchor) == 1
                            src = src.replace(new_anchor, old_anchor, 1)
                    (dst / name).write_text(src, encoding='utf-8')
                if name == SH_FILE:
                    # The local working snapshot already includes the launcher
                    # integration; restore just its original single anchor.
                    from apply_omx_act_prefetch import SH_ANCHOR, SH_REPLACEMENT
                    old = (dst / name).read_text(encoding='utf-8')
                    if 'OMX_PREFETCH_ENV_FILE' in old:
                        if SH_REPLACEMENT in old:
                            old = old.replace(SH_REPLACEMENT, SH_ANCHOR, 1)
                        else:  # Support the earlier local development fixture.
                            start = old.index('# Local opt-in settings shared by all OMX robots.')
                            end = old.index(SH_ANCHOR, start)
                            old = old[:start] + old[end:]
                        assert 'OMX_PREFETCH_ENV_FILE' not in old
                        (dst / name).write_text(old, encoding='utf-8')
            before = {name: (dst / name).read_bytes() for name in [*PY_CHANGES, SH_FILE]}
            changes = patches_for(dst)
            assert len(changes) == 3
            for name, data in before.items():
                assert (dst / name).read_bytes() == data, 'preflight wrote source'
            for path, new in changes:
                path.write_text(new, encoding='utf-8')
            for name in PY_CHANGES:
                ast.parse((dst / name).read_text(encoding='utf-8'))
            if os.name != 'nt' and shutil.which('bash'):
                subprocess.run(['bash', '-n', str(dst / SH_FILE)], check=True)
            try:
                patches_for(dst)
            except RuntimeError:
                pass
            else:
                raise AssertionError('Duplicate patch must be rejected')
            assert 'create_omx_sync_engine(' in (dst / 'lerobot_unified/src/lerobot/rollout/order_models.py').read_text()
            assert 'source "$OMX_PREFETCH_ENV_FILE"' in (dst / SH_FILE).read_text()
        print(f'[PASS] integration {i+1}/{args.runs}: exact 3-file patch, syntax, dry-run, idempotence')
    print(f'[PASS] {args.runs} source-based integration rounds (no hardware)')

if __name__ == '__main__':
    main()
