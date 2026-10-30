#!/usr/bin/env python3
"""Independent hardware-free validation rounds; no installs or robot/broker connections."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TESTS=['tests/test_manager.py','tests/test_follower_bus_lifecycle.py','tests/test_home_motion.py','tests/test_shell_shortcuts.py','tests/test_diagnostics.py','tests/test_service.py','tests/test_workstation_protocol.py','tests/test_unload_client_protocol.py','tests/test_workflow_recovery.py','tests/test_cycle_counting.py','tests/test_rgb_roi.py','tests/test_low_exposure.py']

def fingerprint():
    paths=set(subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0'))
    selected={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths if p and Path(p).suffix in ('.py','.yaml','.sh','.service') and (ROOT/p).is_file()}
    return hashlib.sha256(json.dumps(selected,sort_keys=True).encode()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runs',type=int,default=5)
    args=parser.parse_args()
    if not 1<=args.runs<=1000:parser.error('--runs must be 1..1000')
    directory=ROOT/'reports/repeated_validation';directory.mkdir(parents=True,exist_ok=True)
    environment=os.environ.copy();environment.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT))
    temporary=ROOT/'tests/manager_tmp/system';temporary.mkdir(parents=True,exist_ok=True);environment['TMPDIR']=str(temporary)
    signature=fingerprint();started=time.monotonic();rounds=[]
    try:
        for index in range(1,args.runs+1):
            environment['OMX_TEST_SEED']=str(index)
            output=directory/f'round_{index:03d}.log';begun=time.monotonic();count=None;code=0
            commands=[[sys.executable,'-m','pytest',*TESTS,'-q','-p','no:cacheprovider',f'--basetemp=tests/manager_tmp/repeat_{index:03d}'],
                      [sys.executable,'scripts/test_omx_mqtt_faults.py','--runs','1'],
                      [sys.executable,'scripts/verify_omx.py','--runs','1']]
            with output.open('w') as stream:
                for command in commands:
                    proc=subprocess.Popen(command,cwd=ROOT,env=environment,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
                    try:code=proc.wait(timeout=90)
                    except (subprocess.TimeoutExpired,KeyboardInterrupt):
                        try:os.killpg(proc.pid,signal.SIGTERM)
                        except ProcessLookupError:pass
                        try:proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=3)
                        raise
                    if code:break
            match=re.search(r'(\d+) passed in',output.read_text());count=int(match[1]) if match else 0
            rounds.append({'round':index,'seed':index,'pytest_passed':count,'success':code==0,'seconds':round(time.monotonic()-begun,3),'log':str(output.relative_to(ROOT))})
            print(f'ROUND {index}/{args.runs}: {"PASS" if code==0 else "FAIL"}, pytest={count}',flush=True)
            if code:break
    finally:
        unchanged=signature==fingerprint()
        report={'requested_rounds':args.runs,'completed_rounds':len(rounds),'all_passed':len(rounds)==args.runs and all(r['success'] for r in rounds) and unchanged,
                'source_fingerprint':signature,'source_unchanged':unchanged,'elapsed_s':round(time.monotonic()-started,3),
                'pytest_checks':sum(r['pytest_passed'] for r in rounds),'mock_protocol_checks':12*sum(r['success'] for r in rounds),
                'target':'Raspberry Pi 5 4GB configuration; host-side virtual checks only',
                'physical_hardware_tested':False,'real_model_inference_tested':False,'systemd_daemon_tested':False,'rounds':rounds}
        (ROOT/'reports/repeated_validation.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['all_passed'] else 1

if __name__=='__main__':raise SystemExit(main())
