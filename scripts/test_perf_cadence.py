#!/usr/bin/env python3
"""Five dry-run checks of an opt-in diagnostic patch; no motors."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock
import ast

p=Path(__file__).with_name('apply_perf_cadence.py')
spec=importlib.util.spec_from_file_location('perf_patch',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
fixture='''import os
import time
class Strategy:
    def run(self, ctx, obs_processed):
        start_time = time.perf_counter()
        while True:
                with timer.section("query"):
                    engine.pump_query(obs_processed)

                timer.wait()
'''
for i in range(1,6):
    patched=m.transform(fixture)
    ast.parse(patched)
    assert patched.count('timer.wait()')==1
    assert patched.count('timer.log_episode_summary("OMX performance window")')==1
    assert 'perf_interval_s if perf_interval_s else float("inf")' in patched
    try:m.transform(patched)
    except ValueError:pass
    else:raise AssertionError('duplicate patch accepted')
    assert fixture.count('OMX_PERF_REPORT_INTERVAL_S')==0
    print(f'[PASS] performance patch fixture {i}/5')
print('[PASS] 5 dry-run fixture checks; physical timing not measured')
