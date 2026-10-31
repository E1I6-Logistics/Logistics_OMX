#!/usr/bin/env python3
"""Non-actuating fault injection against actual OMX MQTT function bodies.

No imports of the hardware package, broker connections, or subprocess execution.
"""
import argparse
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import sys
from types import SimpleNamespace
from unittest.mock import Mock

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from omx_manager.journal import JobJournal
TARGET = BASE / 'lerobot_unified' / 'omx_mqtt_client.py'
NAMES = {'execute_orders', 'run_job', 'on_command'}

class FakeClock:
    def __init__(self, step=0.0):
        self.t = 0.0
        self.step = step
    def monotonic(self):
        self.t += self.step
        return self.t
    def sleep(self, s):
        self.t += s

class FakeState:
    def __init__(self, state):
        self.state = state
    def exists(self):
        return True
    def read_text(self):
        return json.dumps(self.state)

class Counter:
    def __init__(self):
        self.calls = []
    def __call__(self, *args):
        self.calls.append(args)

def load_functions():
    src = TARGET.read_text(encoding='utf-8')
    tree = ast.parse(src, filename=str(TARGET))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in NAMES]
    if {n.name for n in nodes} != NAMES:
        raise AssertionError('Required functions missing')
    # AST compiles exact function bodies from source; top-level CLI/MQTT setup is never executed.
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    return compile(module, str(TARGET), 'exec')

def setup(code, state=None, clock=None, pid='12345', dead=False):
    clock = clock or FakeClock()
    state = state if state is not None else dict(order_item='A', order_quantity=1,
        order_started_at_epoch=42, order_completed=0, remaining=1)
    publishes = {'ack':Counter(), 'status':Counter(), 'result':Counter(), 'progress':Counter()}
    def kill(p, sig):
        if dead:
            raise ProcessLookupError('simulated dead process')
        assert p == 12345 and sig == 0
    env = {'OMX_PICKUP_ORDER_TIMEOUT_S':'1'}
    if pid is not None:
        env['OMX_ROLLOUT_PID'] = pid
    fs = FakeState(state)
    abort_tmp = tempfile.TemporaryDirectory()
    env['OMX_ABORT_FILE'] = str(Path(abort_tmp.name)/'abort.flag')
    journal=JobJournal(Path(abort_tmp.name)/'jobs.json')
    journal.begin('job',{'A':1})
    ns = dict(
        time=SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep),
        os=SimpleNamespace(environ=env, kill=kill),
        json=json, Path=Path, subprocess=SimpleNamespace(run=lambda *args,**kwargs:SimpleNamespace(returncode=0)),
        new_order_script='unused.sh', state_path=fs, elapsed=lambda t:str(t),
        send_progress=publishes['progress'], send_status=publishes['status'],
        send_ack=publishes['ack'], send_result=publishes['result'],
        robot_id='omx1', PICKUP_ITEMS={'omx1':('A','B')}, WORKSTATION_IDS=('omx3','omx4'),
        current_job_id=None, current_orders=None,current_state='IDLE',
        job_lock=threading.Lock(), workstation_ready=lambda:True,
        execute_unload=lambda *args:None, make_orders=lambda d:[(k,v) for k,v in d.items() if v],
        threading=threading, journal=journal,
    )
    exec(code, ns)
    return ns, fs, clock, publishes, abort_tmp

def raises(expected,fn):
    try: fn()
    except expected: return
    except Exception as exc: raise AssertionError(f'Expected {expected}, got {type(exc)}: {exc}') from exc
    raise AssertionError(f'Expected {expected.__name__}, returned successfully')

def scenario(code, name):
    if name == 'complete':
        n,s,c,p,t = setup(code, state={'order_item':'A','order_quantity':1,'order_started_at_epoch':42,'order_completed':1,'remaining':0})
        try:
            with contextlib.redirect_stdout(io.StringIO()):n['execute_orders']('job',[('A',1)])
            assert p['progress'].calls == [('job',1,1)]
        finally:t.cleanup()
    elif name == 'timeout':
        n,s,c,p,t=setup(code, clock=FakeClock(step=.25))
        try:
            with contextlib.redirect_stdout(io.StringIO()):raises(TimeoutError,lambda:n['execute_orders']('job',[('A',1)]))
            assert len(p['progress'].calls) <= 1
        finally:t.cleanup()
    elif name == 'dead_pid':
        n,s,c,p,t=setup(code,dead=True)
        try:
            with contextlib.redirect_stdout(io.StringIO()):raises(RuntimeError,lambda:n['execute_orders']('job',[('A',1)]))
        finally:t.cleanup()
    elif name == 'missing_pid':
        n,s,c,p,t=setup(code,pid=None)
        try:
            with contextlib.redirect_stdout(io.StringIO()):raises(RuntimeError,lambda:n['execute_orders']('job',[('A',1)]))
        finally:t.cleanup()
    elif name == 'corrupt_state':
        n,s,c,p,t=setup(code)
        try:
            s.read_text=lambda:'{not-json'
            with contextlib.redirect_stdout(io.StringIO()):raises(json.JSONDecodeError,lambda:n['execute_orders']('job',[('A',1)]))
        finally:t.cleanup()
    elif name == 'failure_reject':
        n,s,c,p,t=setup(code,dead=True)
        try:
            assert n['job_lock'].acquire(False)
            n['current_job_id']='job'; n['current_orders']=(('A',1),)
            with contextlib.redirect_stdout(io.StringIO()): n['run_job']('job',[('A',1)])
            assert n['current_state']=='ERROR'
            assert p['result'].calls and p['result'].calls[-1][1] is False
            assert Path(n['os'].environ['OMX_ABORT_FILE']).exists(), 'Abort signal not generated'
            assert not n['job_lock'].locked()
            with contextlib.redirect_stdout(io.StringIO()): n['on_command']({'job_id':'retry','items':{'A':1}})
            assert p['ack'].calls[-1]==('retry',False)
            assert not n['job_lock'].locked()
        finally:t.cleanup()
    else:raise KeyError(name)

SCENARIOS=('complete','timeout','dead_pid','missing_pid','corrupt_state','failure_reject')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runs',type=int,default=5)
    args=parser.parse_args()
    if not 1 <= args.runs <= 100:parser.error('--runs must be 1..100')
    code=load_functions()
    for i in range(args.runs):
        for s in SCENARIOS:
            scenario(code,s)
        print(f'[PASS] round {i+1}/{args.runs}: {len(SCENARIOS)} scenarios')
    print(f'[PASS] {args.runs * len(SCENARIOS)} mock checks; NO broker, subprocess, or motor commands')

if __name__=='__main__':main()
