#!/usr/bin/env python3
"""Patch three stable integration points without touching weights or safety logic.

Dry-run (`--check`) validates ALL targets before any writes. The default
inference mode remains unchanged until local opt-in via set_omx_optimization.
"""
from __future__ import annotations

import argparse
import os
import shutil
import ast
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_CHANGES = {
    "lerobot_unified/src/lerobot/rollout/inference/factory.py": (
        ("from .sync import SyncInferenceEngine", "from .act_prefetch import create_omx_sync_engine"),
        ("return SyncInferenceEngine(", "return create_omx_sync_engine("),
    ),
    "lerobot_unified/src/lerobot/rollout/order_models.py": (
        ("from .inference.sync import SyncInferenceEngine", "from .inference.sync import SyncInferenceEngine\nfrom .inference.act_prefetch import create_omx_sync_engine"),
        ("engine = SyncInferenceEngine(", "engine = create_omx_sync_engine("),
    ),
}
SH_FILE = "lerobot_unified/run_omx_backend.sh"
SH_ANCHOR = 'source "$SCRIPT_DIR/omx_runtime_env.sh"'
SH_REPLACEMENT = '''# Optional local optimization settings (all four OMX launch through this script).
OMX_PREFETCH_ENV_FILE="${OMX_PREFETCH_ENV_FILE:-$HOME/.config/omx/act_prefetch.env}"
if [[ -f "$OMX_PREFETCH_ENV_FILE" ]]; then
  source "$OMX_PREFETCH_ENV_FILE"
fi
''' + SH_ANCHOR


def patches_for(root: Path) -> list[tuple[Path, str]]:
    patched: list[tuple[Path, str]] = []
    for name, replacements in PY_CHANGES.items():
        file = root / name
        source = file.read_text(encoding="utf-8")
        for old, new in replacements:
            if source.count(old) != 1:
                raise RuntimeError(f"[FAIL] {name}: expected 1 occurrence of {old!r}, got {source.count(old)}")
            source = source.replace(old, new, 1)
        ast.parse(source, filename=name)
        patched.append((file, source))
    file = root / SH_FILE
    source = file.read_text(encoding="utf-8")
    if source.count(SH_ANCHOR) != 1 or 'OMX_PREFETCH_ENV_FILE' in source:
        raise RuntimeError("[FAIL] launcher integration missing or already applied")
    source = source.replace(SH_ANCHOR, SH_REPLACEMENT, 1)
    # Windows PowerShell may have neither Bash nor a Windows-compatible shell.
    # Perform Bash's own parser check on Linux; exact source anchors always checked.
    if os.name != "nt" and shutil.which("bash"):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", encoding="utf-8") as temp:
            temp.write(source)
            temp.flush()
            subprocess.run(["bash", "-n", temp.name], check=True)
    patched.append((file, source))
    return patched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    module = ROOT / "lerobot_unified/src/lerobot/rollout/inference/act_prefetch.py"
    if not module.is_file():
        raise SystemExit("[FAIL] Missing new act_prefetch.py; unpack ZIP in repository root")
    ast.parse(module.read_text(encoding="utf-8"))
    targets = patches_for(ROOT)
    if args.check:
        print("[PASS] factory + model router + launcher + module: all preflight checks; files untouched")
        return
    for file, _ in targets:
        backup = file.with_name(file.name + ".bak_act_prefetch")
        if backup.exists():
            raise RuntimeError(f"[FAIL] Existing backup: {backup}")
    for file, new_source in targets:
        file.with_name(file.name + ".bak_act_prefetch").write_bytes(file.read_bytes())
        file.write_text(new_source, encoding="utf-8", newline="\n")
        print(f"[PATCH] {file.relative_to(ROOT)}")
    print("[PASS] integrated. Default OFF until explicitly enabled on each device")


if __name__ == "__main__":
    main()
