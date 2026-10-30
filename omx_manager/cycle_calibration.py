"""Learn return regions from supervised ACT motion, never from manual pick poses."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import uuid
from collections import deque

from .config import ConfigError,check_files,digest,load,profile,require_home,save,write_json,diagnostic_options
from .cycles import ARM,PRIMARY,learn_profile,options
from .home import joints
from .hooks import heartbeat,initialize_home


def read_json(path):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return {}


def parent_alive():
    pid=os.environ.get('OMX_CALIBRATION_PARENT_PID')
    if not pid:return True
    try:
        stat=Path('/proc',pid,'stat').read_text()
        fields=stat[stat.rfind(')')+2:].split()
        return fields[0]!='Z' and fields[19]==os.environ.get('OMX_CALIBRATION_PARENT_START')
    except (OSError,IndexError):return False


def action_paused():
    if os.environ.get('OMX_CYCLE_CALIBRATION')!='1':return False
    if not parent_alive():return True
    row=read_json(os.environ['OMX_CALIBRATION_COMMAND'])
    return row.get('session')!=os.environ['OMX_SESSION'] or row.get('command')!='START'


class ReturnNotSettled(ConfigError):
    """A rejected observation window; no model resume or calibration save."""


def collect_return(device,cfg,clock,sleep,stopped):
    spec=options(cfg);start=clock();last=None;window=deque();reason='WAIT_SAMPLES'
    limit=cfg['home']['capture_max_span']
    while clock()-start<spec['calibration_settle_timeout_s']:
        if stopped() or not parent_alive():raise InterruptedError('Operator stopped calibration')
        q=joints(device.get_joint_observation());now=clock()
        if last is not None and not 0<now-last<=cfg['home']['max_sample_gap_s']:
            window.clear();reason='FEEDBACK_GAP: continuous stable window restarted'
        if any(abs(q[j]-cfg['home']['target'][j])>cfg['home']['max_start_error'] for j in ARM):
            raise ConfigError('Arm has not returned near Home; stretched positions cannot be learned')
        if any(abs(q[j]-cfg['home']['target'][j])>cfg['home']['tolerance'][j] for j in PRIMARY):
            raise ConfigError('Shoulder/elbow have not returned near original Home')
        # Keep only a continuous, stable suffix. Initial deceleration or one
        # outlier cannot poison every later window, or contribute to hold time.
        window.append((now,q));last=now
        while len(window)>1 and window[1][0]<=now-spec['calibration_hold_s']:
            window.popleft()
        spans={j:max(row[j] for _,row in window)-min(row[j] for _,row in window) for j in ARM}
        moving={j:span for j,span in spans.items() if span>limit}
        if moving:
            reason='; '.join(f'{j} span={span:.3f} limit={limit:.3f}' for j,span in sorted(moving.items(),key=lambda row:-row[1]))
            # Discard unstable history until the remaining suffix is stable.
            while len(window)>1 and any(max(row[j] for _,row in window)-min(row[j] for _,row in window)>limit for j in ARM):
                window.popleft()
        heartbeat()
        if stopped() or not parent_alive():raise InterruptedError('Operator stopped calibration')
        if len(window)>=3 and now-window[0][0]+1e-9>=spec['calibration_hold_s']:
            return [row for _,row in window]
        sleep(.05)
    raise ReturnNotSettled(f'Return did not settle in {spec["calibration_settle_timeout_s"]:g}s; {reason}. Model paused; no sample saved')


def run_rollout(strategy,ctx,send_action,make_timer):
    cfg=getattr(ctx.hardware.robot_wrapper.inner,'_omx_manager_snapshot')
    raw=ctx.hardware.robot_wrapper.inner;robot=ctx.hardware.robot_wrapper;stop=ctx.runtime.shutdown_event
    command=Path(os.environ['OMX_CALIBRATION_COMMAND']);status=Path(os.environ['OMX_CALIBRATION_STATUS'])
    output=Path(os.environ['OMX_CALIBRATION_OUTPUT']);session=os.environ['OMX_SESSION']
    item=os.environ['OMX_CALIBRATION_ITEM'];engine=strategy._engine
    timer=make_timer(ctx.runtime.cfg.fps,strategy._interpolator.multiplier,records_data=False,report=ctx.runtime.cadence_report)
    phase='READY';applied=0;captures=[];trajectory=[];actions=0;started=0
    def publish(new_phase,**extra):
        nonlocal phase
        phase=new_phase
        write_json(status,dict(session=session,phase=phase,seq=applied,item=item,cycles=len(captures),pid=os.getpid(),at=time.time(),**extra))
    strategy.reset_control_state();engine.pause();publish('READY')
    try:
        while not stop.is_set():
            if not parent_alive():
                stop.set();return # owner died: clean up, never move Home automatically
            heartbeat()
            cmd=read_json(command)
            if cmd.get('session')==session and type(cmd.get('seq')) is int and cmd['seq']>applied:
                applied=cmd['seq'];operation=cmd.get('command')
                if operation=='STOP':return
                if operation=='START' and phase=='READY':
                    strategy.reset_control_state();engine.resume();timer.restart()
                    trajectory=[];actions=0;started=time.perf_counter();publish('RUNNING')
                elif operation=='CONFIRM' and phase in ('RUNNING','CAPTURE_RETRY'):
                    engine.pause();strategy.reset_control_state()
                    if actions<options(cfg)['min_actions']:raise ConfigError('Not enough model actions to confirm a cycle')
                    if phase=='RUNNING':duration=time.perf_counter()-started
                    publish('SETTLING')
                    try:samples=collect_return(raw,cfg,time.perf_counter,time.sleep,stop.is_set)
                    except ReturnNotSettled as exc:
                        publish('CAPTURE_RETRY',reason=str(exc));continue
                    captures.append(dict(confirmed=True,duration_s=duration,actions=actions,trajectory=trajectory,return_samples=samples))
                    write_json(output,dict(item=item,session=session,captures=captures))
                    publish('CAPTURED')
                elif operation=='HOME' and phase=='CAPTURED':
                    # Explicit supervised calibration: operator has confirmed transfer.
                    publish('ALIGNING');initialize_home(raw,stopped=stop.is_set);publish('READY')
                else:raise ConfigError('Invalid calibration command/state; no automatic restart')
            if phase!='RUNNING':stop.wait(.05);continue
            if time.perf_counter()-started>cfg['runtime']['cycle_timeout_s']:
                raise TimeoutError('Calibration cycle timeout; no automatic success label')
            timer.tick(new_cycle=strategy._interpolator.needs_new_action())
            obs,reconnected=strategy._get_observation_with_camera_recovery(robot,engine)
            if reconnected:raise RuntimeError('Camera reconnected during calibration; repeat this example')
            trajectory.append(joints(obs))
            if len(trajectory)>12000:raise RuntimeError('Calibration trace size limit reached')
            processed=strategy._process_observation_and_notify(ctx.processors,obs)
            if stop.is_set():break
            if send_action(processed,obs,ctx,strategy._interpolator,timer) is not None:actions+=1
            timer.wait()
    except Exception as exc:
        publish('ERROR',reason=str(exc));raise
    finally:
        engine.pause();strategy.reset_control_state()


def run(config_path,item):
    """Called under device_lock; launches no MQTT worker and sends no FMS order."""
    from .runtime import ROOT,build,state_dir,stop_process
    from .hardware import FollowerBus
    from .logs import Capture
    from .home import errors
    cfg=load(config_path)
    if item not in profile(cfg)['items']:raise ConfigError('Choose a model item for this device')
    check_files(cfg);require_home(cfg)
    initial=digest(config_path)
    with FollowerBus(cfg) as device:
        if max(errors(device.read(),cfg['home']['target']).values())>cfg['home']['arrival_tolerance']:
            raise ConfigError('Start calibration at the existing strict Home; use omx_home first')
        for j in device.bus.motors:
            if device.bus.read('Torque_Enable',j)!=1 or device.bus.read('Operating_Mode',j)!=(5 if j=='gripper' else 4):
                raise ConfigError('Confirm local arm torque/mode before model calibration')
    session=uuid.uuid4().hex;directory=state_dir(cfg)/'cycle_calibration'/session;directory.mkdir(parents=True)
    command=directory/'command.json';status=directory/'status.json';output=directory/'captures.json'
    argv,_,env,_=build(cfg,config_path,session=session,calibration_item=item)
    env.update(OMX_CALIBRATION_COMMAND=str(command),OMX_CALIBRATION_STATUS=str(status),OMX_CALIBRATION_OUTPUT=str(output),
               OMX_MANAGER_HEARTBEAT=str(directory/'heartbeat.json'),OMX_METRICS_PATH=str(directory/'metrics.json'),
               OMX_ABORT_FILE=str(directory/'abort'))
    stat=Path('/proc/self/stat').read_text()
    env.update(OMX_CALIBRATION_PARENT_PID=str(os.getpid()),OMX_CALIBRATION_PARENT_START=stat[stat.rfind(')')+2:].split()[19])
    write_json(command,dict(session=session,seq=0,command='WAIT'))
    logs=Capture(ROOT/'logs'/cfg['device_id'],True,diagnostic_options(cfg));worker=None;seq=0
    def send(operation):
        nonlocal seq
        seq+=1;write_json(command,dict(session=session,seq=seq,command=operation))
        return seq
    def check_worker():
        if worker.poll() is not None:
            raise RuntimeError('Calibration rollout exited: '+str(read_json(status).get('reason',worker.returncode)))
        row=read_json(status)
        if row.get('phase')=='ERROR':raise RuntimeError(row.get('reason','Calibration error'))
        return row
    def wait_phase(expected,sequence,timeout):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            row=check_worker()
            if row.get('session')==session and row.get('seq',-1)>=sequence:
                if row.get('phase')==expected:return row
                if expected=='CAPTURED' and row.get('phase')=='CAPTURE_RETRY':return row
            time.sleep(.1)
        raise TimeoutError('Calibration phase timeout: '+expected)
    def enter(prompt):
        print(prompt,flush=True)
        while True:
            check_worker()
            if select.select([sys.stdin],[],[],.2)[0]:
                answer=sys.stdin.readline()
                if not answer or answer.strip().lower()=='q':raise KeyboardInterrupt()
                if not answer.strip():return
                print('Enter=진행, q=취소',flush=True)
    try:
        worker=subprocess.Popen(argv,env=env,cwd=ROOT/'lerobot_unified',start_new_session=True,
                                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        logs.attach(worker,'calibration')
        wait_phase('READY',0,cfg['runtime']['startup_timeout_s'])
        required=options(cfg)['min_training_cycles']
        for index in range(required):
            enter(f'[보정 {item} {index+1}/{required}] 물품·작업 공간을 준비하고 손을 뗀 뒤 Enter: 모델 동작 시작 / q: 취소')
            wait_phase('RUNNING',send('START'),cfg['runtime']['startup_timeout_s'])
            enter('실제로 집기·놓기·복귀를 마쳤을 때 Enter: 정상 사이클 확인. 헛집기/미완료는 q로 취소.')
            while True:
                row=wait_phase('CAPTURED',send('CONFIRM'),options(cfg)['calibration_settle_timeout_s']+10)
                if row['phase']=='CAPTURED':break
                print('[보정 재확인] '+row['reason'],flush=True)
                enter('모델은 정지 상태입니다. 팔을 만지지 말고 안정된 정상 복귀 자세인지 확인 후 Enter: 관측만 재시도 / q: 취소')
            print('정상 복귀 기록 완료. 다음 시작을 위해 기존 고정 홈으로 제한 이동합니다.',flush=True)
            wait_phase('READY',send('HOME'),cfg['home']['move_timeout_s']+5)
        send('STOP')
        worker.wait(timeout=cfg['runtime']['shutdown_timeout_s'])
        if worker.returncode:raise RuntimeError('Calibration cleanup failed; settings unchanged')
        learned=learn_profile(cfg,item,read_json(output)['captures'])
        if digest(config_path)!=initial:raise ConfigError('YAML changed during calibration; refusing overwrite')
        require_home(cfg)
        cfg['counting']['mode']='cycle';cfg['counting'].setdefault('profiles',{})[item]=learned
        save(config_path,cfg)
        print(f'[보정 저장] {item}: {required}회 정상 확인. pick/place 등록 없이 이 복귀 영역으로 동작 사이클을 카운트합니다. 실제 물품 성공 수는 아닙니다.',flush=True)
        metrics=read_json(directory/'metrics.json')
        recorded=metrics.get('timings',{}).get('act_chunk',[0,0.,0.])
        if recorded[0]:print(f'[ACT 실측] 기록된 {recorded[0]}개 청크 평균 {recorded[1]/recorded[0]:.3f}초 / 최대 {recorded[2]:.3f}초. CPU 설정: {metrics.get("cpu_threads")}',flush=True)
        return learned
    finally:
        if worker is not None:stop_process(worker,cfg['runtime']['shutdown_timeout_s'])
        logs.close()
