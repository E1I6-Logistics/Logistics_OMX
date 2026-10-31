#!/usr/bin/env python3
"""Inject opt-in periodic cadence diagnostics without changing robot control."""
import argparse
import ast
from pathlib import Path

TARGET=Path('lerobot_unified/src/lerobot/rollout/strategies/base.py')
ANCHOR_A='        start_time = time.perf_counter()\n'
REPLACE_A='''        start_time = time.perf_counter()
        # Optional per-window performance profiling; no change to motion commands.
        perf_interval_s = float(os.environ.get("OMX_PERF_REPORT_INTERVAL_S", "0"))
        if perf_interval_s < 0:
            raise ValueError("OMX_PERF_REPORT_INTERVAL_S must be >= 0")
        perf_next_report = start_time + perf_interval_s if perf_interval_s else float("inf")
'''
ANCHOR_B='''                with timer.section("query"):
                    engine.pump_query(obs_processed)

                timer.wait()
'''
REPLACE_B='''                with timer.section("query"):
                    engine.pump_query(obs_processed)

                timer.wait()
                if time.perf_counter() >= perf_next_report:
                    timer.log_episode_summary("OMX performance window")
                    perf_next_report = time.perf_counter() + perf_interval_s
'''

def transform(src):
    if 'OMX_PERF_REPORT_INTERVAL_S' in src:
        raise ValueError('Already patched')
    for old in (ANCHOR_A, ANCHOR_B):
        if src.count(old)!=1:
            raise ValueError('Expected one anchor, got %d: %r' % (src.count(old),old[:55]))
    out=src.replace(ANCHOR_A,REPLACE_A,1).replace(ANCHOR_B,REPLACE_B,1)
    ast.parse(out)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check', action='store_true'); args=ap.parse_args()
    src=TARGET.read_text(encoding='utf-8')
    transformed=transform(src)
    if args.check:
        print('[PASS] source anchors, safe transformation, Python AST; file unchanged')
        return
    backup=TARGET.with_suffix('.py.bak_perf')
    if backup.exists():
        raise SystemExit('[FAIL] Backup already exists: '+str(backup))
    backup.write_bytes(TARGET.read_bytes())
    TARGET.write_text(transformed, encoding='utf-8', newline='\n')
    print('[PASS] optional cadence diagnostics applied; backup='+str(backup))
if __name__=='__main__':main()
