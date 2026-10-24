#!/usr/bin/env python3
"""Phase 1: remove only historical OMX copies; never modify active runtime.

Default: preview. --apply: git rm explicitly identified legacy paths.
Requires the branch to be exactly refactor/omx-unified-runtime and clean
except for the two new files shipped in this package.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

BRANCH = "refactor/omx-unified-runtime"
BASE_SHA_PREFIX = "e2d9dd3"
ROOT = Path(__file__).resolve().parents[1]

# The historical overlay is not imported by the active unified runtime.
# The two active role wrappers and profiles are deliberately excluded.
LEGACY_PATHS = (
    "robots_ws/src/omx_manipulator/overlay/",
    "robots_ws/src/omx_manipulator/overlay_workstation/",
    "scripts/setup_omx_lerobot.sh",
    "robots_ws/src/omx_manipulator/runtime/omx_cd_new_order.sh",
    "robots_ws/src/omx_manipulator/runtime/omx_cd_order_console.py",
    "robots_ws/src/omx_manipulator/runtime/omx_cd_order_console.sh",
    "robots_ws/src/omx_manipulator/runtime/omx_cd_order_models.json",
    "robots_ws/src/omx_manipulator/runtime/omx_mqtt_client.py",
    "robots_ws/src/omx_manipulator/runtime/omx_new_order.sh",
    "robots_ws/src/omx_manipulator/runtime/omx_order_console.py",
    "robots_ws/src/omx_manipulator/runtime/omx_order_console.sh",
    "robots_ws/src/omx_manipulator/runtime/omx_order_models.json",
    "robots_ws/src/omx_manipulator/runtime/omx_runtime_env.sh",
    "robots_ws/src/omx_manipulator/runtime/omx_status.sh",
    "robots_ws/src/omx_manipulator/runtime/run_ab_orders.sh",
    "robots_ws/src/omx_manipulator/runtime/run_cd_orders.sh",
    "robots_ws/src/omx_manipulator/runtime/run_e_rollout.sh",
    "robots_ws/src/omx_manipulator/runtime/run_omx_backend.sh",
)

MUST_KEEP = (
    "run_omx_mqtt.sh",
    "scripts/omx_shell.sh",
    "scripts/install_omx.sh",
    "scripts/preflight_omx.sh",
    "robots_ws/src/omx_manipulator/runtime/run_omx_pickup.sh",
    "robots_ws/src/omx_manipulator/runtime/run_omx_unload.sh",
    *tuple(f"robots_ws/src/omx_manipulator/runtime/profiles/omx{i}.env.example" for i in range(1, 5)),
    "lerobot_unified/run_omx_backend.sh",
    "lerobot_unified/omx_mqtt_client.py",
    "lerobot_unified/omx_new_order.sh",
    "lerobot_unified/run_e_rollout.sh",
    "lerobot_unified/run_f_rollout.sh",
    "lerobot_unified/src/lerobot/rollout/strategies/base.py",
    "lerobot_unified/src/lerobot/rollout/inference/sync.py",
    "lerobot_unified/src/lerobot/rollout/inference/factory.py",
    "lerobot_unified/src/lerobot/rollout/inference/act_prefetch.py",
    "scripts/set_omx_optimization.sh",
)

ALLOWED_NEW = {
    "tools/refactor_phase1.py",
    "doc/ARCHITECTURE_CURRENT.md",
}


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def tracked() -> set[str]:
    return set(git("ls-files").splitlines())


def target_files(all_files: set[str]) -> list[str]:
    result = sorted(
        path for path in all_files
        if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in LEGACY_PATHS)
    )
    return result


def ensure_clean_except_package() -> None:
    dirty = git("status", "--porcelain=v1", "--untracked-files=all")
    for line in dirty.splitlines():
        code, path = line[:2], line[3:].replace("\\", "/")
        if code != "??" or path not in ALLOWED_NEW:
            raise RuntimeError(f"Unexpected working tree change: {line}. Stop and inspect before applying.")


def check_baseline() -> None:
    branch = git("branch", "--show-current")
    if branch != BRANCH:
        raise RuntimeError(f"Expected branch {BRANCH}, got {branch!r}")
    sha = git("rev-parse", "HEAD")
    if not sha.startswith(BASE_SHA_PREFIX):
        raise RuntimeError(f"Expected baseline commit {BASE_SHA_PREFIX}, got {sha[:10]}")
    ensure_clean_except_package()
    files = tracked()
    missing = [name for name in MUST_KEEP if name not in files or not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError("Missing active files; refusing cleanup: " + ", ".join(missing))
    # Verify exact execution chain. No files that this script deletes are referenced here.
    chain = {
        "run_omx_mqtt.sh": ["run_omx_${ROLE}.sh"],
        "robots_ws/src/omx_manipulator/runtime/run_omx_pickup.sh": ["lerobot_unified/run_omx_backend.sh"],
        "robots_ws/src/omx_manipulator/runtime/run_omx_unload.sh": ["lerobot_unified/run_omx_backend.sh"],
        "scripts/install_omx.sh": ["runtime/profiles/"],
    }
    for name, required_fragments in chain.items():
        text = (ROOT / name).read_text(encoding="utf-8")
        if not all(fragment in text for fragment in required_fragments):
            raise RuntimeError(f"Active entrypoint has changed, manual review required: {name}")
    for name in ["run_omx_mqtt.sh", "lerobot_unified/run_omx_backend.sh"]:
        text = (ROOT / name).read_text(encoding="utf-8")
        if "overlay/" in text or "setup_omx_lerobot.sh" in text:
            raise RuntimeError(f"Unexpected legacy dependency in {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Execute exact git rm after guards")
    parser.add_argument("--verify", action="store_true", help="After apply: ensure paths removed and active files remain")
    args = parser.parse_args()
    if args.apply and args.verify:
        parser.error("--apply and --verify are mutually exclusive")
    if args.verify:
        if git("branch", "--show-current") != BRANCH:
            raise RuntimeError("Wrong branch")
        ls = tracked()
        residual = target_files(ls)
        if residual:
            raise RuntimeError("Legacy files still tracked: " + ", ".join(residual))
        missing = [p for p in MUST_KEEP if p not in ls or not (ROOT / p).is_file()]
        if missing:
            raise RuntimeError("Active code missing: " + ", ".join(missing))
        print("[PASS] legacy paths removed; protected runtime files remain tracked")
        print("[NOTE] Static checks only; actual motor behavior is untested")
        return

    check_baseline()
    files = tracked()
    selected = target_files(files)
    if not selected:
        raise RuntimeError("Nothing selected; possible wrong checkout or already cleaned")
    print(f"[PLAN] branch={BRANCH} base={git('rev-parse', '--short', 'HEAD')}; {len(selected)} tracked files to delete")
    for p in selected:
        print("  -", p)
    print("[KEEP] both role wrappers, 4 profiles, entire lerobot_unified and all active control code")
    if not args.apply:
        print("[DRY-RUN] No changes made. To apply: python tools/refactor_phase1.py --apply")
        return
    # No shell. Explicit exact file list. git rm stages deletions for review.
    subprocess.run(["git", "rm", "--", *selected], cwd=ROOT, check=True)
    print("[DONE] Legacy copies removed and staged. Review: git diff --cached --stat")
    print("[NEXT] python tools/refactor_phase1.py --verify")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"[STOP] {exc}")
