"""현재 사용자·저장소의 OMX 프로세스 정리. 모터·카메라 API 호출 없음."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import time
from .config import ROOT

SCRIPTS = {'omx_control', 'run_omx_mqtt.sh', 'omx_mqtt_client.py', 'lerobot_rollout.py'}


def inside(value, root):
    try:return Path(value).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):return False


def read_process(pid):
    try:
        path=Path('/proc')/str(pid)
        stat=(path/'stat').read_text();fields=stat[stat.rfind(')')+2:].split()
        if fields[0]=='Z':return None
        args=(path/'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
        env={}
        try:env=dict(x.split('=',1) for x in (path/'environ').read_bytes().decode(errors='replace').split('\0') if '=' in x)
        except OSError:pass
        return dict(pid=int(pid),parent=int(fields[1]),start=int(fields[19]),uid=path.stat().st_uid,
                    args=args,cwd=str((path/'cwd').resolve()),env=env)
    except (OSError, ValueError, IndexError):return None


def inventory():
    return {int(p.name):r for p in Path('/proc').iterdir() if p.name.isdigit() and (r:=read_process(int(p.name))) is not None}


def select(rows, root=ROOT, uid=None, own=None, tracked=None):
    uid=os.getuid() if uid is None else uid;own=os.getpid() if own is None else own
    protected={own,1};parent=rows.get(own,{}).get('parent')
    while parent and parent not in protected:
        protected.add(parent);parent=rows.get(parent,{}).get('parent')
    candidates={pid:r for pid,r in rows.items() if r['uid']==uid and pid not in protected}
    selected={pid:r for pid,r in candidates.items() if tracked and tracked.get(pid)==r['start']}
    for pid,row in candidates.items():
        args=row['args'];module=None
        if '-m' in args:
            i=args.index('-m')
            if i+1<len(args):module=args[i+1]
        if module=='omx_manager.stop_all':continue
        known=bool(module and (module.startswith('omx_manager.') or module=='lerobot.scripts.lerobot_rollout'))
        known=known or any(Path(a).name in SCRIPTS for a in args)
        known=known or (args and Path(args[0]).name=='journalctl' and any(a.startswith('omx-manager@') and a.endswith('.service') for a in args))
        bound=inside(row['cwd'],root) or any(a.startswith('/') and inside(a,root) for a in args)
        bound=bound or inside(row['env'].get('OMX_MANAGER_CONFIG','/'),root)
        if known and bound:selected[pid]=row
    while True:
        added={pid:r for pid,r in candidates.items() if pid not in selected and r['parent'] in selected}
        if not added:break
        selected.update(added)
    return selected


def send(row, sig):
    # A PID may be recycled while shutdown is running. Bind Linux signals to
    # a process handle and recheck start ticks before sending anything.
    try:
        fd=os.pidfd_open(row['pid'])
        try:
            current=read_process(row['pid'])
            if current and current['uid']==row['uid'] and current['start']==row['start']:
                signal.pidfd_send_signal(fd,sig);return True
        finally:os.close(fd)
    except ProcessLookupError:pass
    return False


def stop_services():
    # An explicit stop cancels Restart=always. Do this BEFORE process signals.
    result=subprocess.run(['systemctl','--user','stop','--no-block','omx-manager@*.service'],
                          capture_output=True,text=True,timeout=8)
    if result.returncode:
        raise RuntimeError('자동 재시작 차단 실패: '+result.stderr.strip()+'; omx_service stop 결과를 확인하세요')


def stop_all(timeout=5., dry_run=False):
    if not 0<=timeout<=30:raise ValueError('종료 대기 시간은 0~30초입니다')
    if not dry_run:stop_services()
    rows=select(inventory())
    for row in rows.values():
        # Do not echo arbitrary argv/environment (possibly sensitive values).
        print(f'[KILL] 대상 PID={row["pid"]}',flush=True)
    if dry_run:return rows
    tracked={pid:r['start'] for pid,r in rows.items()}
    for row in rows.values():send(row,signal.SIGINT)
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        remaining=select(inventory(),tracked=tracked)
        for pid,row in remaining.items():
            if tracked.get(pid)!=row['start']:
                tracked[pid]=row['start'];send(row,signal.SIGINT)
        if not remaining:break
        time.sleep(.1)
    remaining=select(inventory(),tracked=tracked)
    for row in remaining.values():
        if send(row,signal.SIGKILL):print(f'[KILL] 응답 없는 PID={row["pid"]} 강제 종료',flush=True)
    end=time.monotonic()+2
    while time.monotonic()<end:
        remaining=select(inventory(),tracked=tracked)
        if not remaining:break
        time.sleep(.1)
    if remaining:raise RuntimeError('아직 종료 대기 중인 PID: '+', '.join(map(str,remaining)))
    print('[KILL] OMX 프로세스 정리 완료. 모터 토크·홈·보정·작업 기록은 변경하지 않았습니다.',flush=True)
    return {}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout',type=float,default=5.,help='정상 종료 대기 초: 기본 5, 이후 남은 프로세스 강제 종료')
    parser.add_argument('--list',action='store_true',dest='dry_run',help='종료 없이 대상 PID만 조회')
    args=parser.parse_args(argv)
    try:stop_all(args.timeout,args.dry_run)
    except (Exception,KeyboardInterrupt) as exc:
        print('[KILL] 종료 미완료:',str(exc));return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
