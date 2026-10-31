"""Bounded explicit motion cycles without basket inspection or exposure changes."""
import logging
import os
import time
from pathlib import Path
from .basket import options
from .config import write_json
from .hooks import heartbeat,initialize_home

logger=logging.getLogger(__name__)


def require_count(value):
    if type(value) is not int or not 1<=value<=8:
        raise ValueError('Basket inspection OFF: specify 1..8 motion cycles; omx_unload 1')
    return value


def run(strategy,ctx,send_action,make_timer):
    from lerobot.rollout.workstation_ipc import atomic_json,new_status,read_json
    cfg=strategy._manager_home.cfg;spec=options(cfg);engine=strategy._engine
    robot=ctx.hardware.robot_wrapper;inner=getattr(robot,'inner',robot)
    stop=ctx.runtime.shutdown_event;session=os.environ['OMX_WORKSTATION_SESSION']
    command_path=Path(strategy.config.workstation_control_file);status_path=Path(strategy.config.workstation_status_file)
    timer=make_timer(ctx.runtime.cfg.fps,strategy._interpolator.multiplier,records_data=False,report=ctx.runtime.cadence_report)
    phase='READY';job=None;target=None;cycles=0;started=cycle_started=0.
    def status(new_phase,**extra):
        nonlocal phase
        phase=new_phase
        row=new_status(session,phase,job,attempts=cycles+(phase=='RUNNING'),completed_cycles=cycles,
                       estimated_transfers=None,expected_count=target,requested_cycles=target,
                       count_kind='motion_cycles',count_is_estimate=True,inspection_scope='disabled',
                       physical_unloading_verified=False,basket_empty_verified=False,
                       visible_region_empty_verified=False,**extra)
        atomic_json(status_path,row);write_json(status_path.with_name('unload_progress.json'),row)
        write_json(status_path.with_name('basket_status.json'),dict(verdict='UNKNOWN',reason='INSPECTION_DISABLED',at=time.time()),durable=False)
    def start_cycle():
        nonlocal cycle_started
        strategy.reset_control_state();timer.restart();cycle_started=time.perf_counter()
        strategy._manager_home.reset(cycle_started);status('RUNNING');engine.resume()
    engine.pause();strategy.reset_control_state();status('READY')
    logger.info('[WORKSTATION] READY: basket inspection OFF; explicit 1..8 motion cycles required; transfers unverified')
    try:
        while not stop.is_set():
            heartbeat();command=read_json(command_path)
            same=command.get('session')==session and command.get('job_id')==job
            if phase=='READY':
                if command.get('session')!=session or command.get('command')!='START' or not command.get('job_id'):
                    stop.wait(strategy.config.workstation_poll_s);continue
                job=str(command['job_id']);cycles=0;target=None
                try:target=require_count(command.get('expected_count'))
                except ValueError as exc:
                    status('JOB_FAILED',message=str(exc),restore_verified=True);continue
                started=time.perf_counter();engine.pause();strategy.reset_control_state()
                initialize_home(inner,stopped=stop.is_set);start_cycle();continue
            if phase in ('DONE','JOB_FAILED'):
                if same and command.get('command')=='CLEAR':
                    strategy.reset_control_state();job=None;target=None;cycles=0;status('READY')
                else:stop.wait(strategy.config.workstation_poll_s)
                continue
            now=time.perf_counter()
            if now-started>spec['job_timeout_s']:raise TimeoutError('Motion-cycle job timeout; no transfer success claimed')
            if now-cycle_started>cfg['runtime']['cycle_timeout_s']:raise TimeoutError('Motion cycle incomplete: departure/Home return timeout')
            timer.tick(new_cycle=strategy._interpolator.needs_new_action())
            obs,reconnected=strategy._get_observation_with_camera_recovery(robot,engine)
            if reconnected:strategy._manager_home.reset(time.perf_counter());timer.restart()
            if strategy._manager_home.update(obs,time.perf_counter()):
                engine.pause();strategy.reset_control_state();initialize_home(inner,stopped=stop.is_set)
                if stop.is_set():break
                cycles+=1
                logger.info('[UNLOAD] motion cycles=%s/%s; basket inspection OFF, transfers unverified',cycles,target)
                if cycles>=target:status('DONE',reason='requested_cycles_completed')
                else:start_cycle()
                continue
            processed=strategy._process_observation_and_notify(ctx.processors,obs)
            if strategy._handle_warmup(ctx.runtime.cfg.use_torch_compile,timer):continue
            if stop.is_set():break
            action=send_action(processed,obs,ctx,strategy._interpolator,timer)
            strategy._log_telemetry(processed,action,ctx.runtime);engine.pump_query(processed);timer.wait()
    except Exception as exc:
        engine.pause();status('ERROR',message=str(exc));raise
    finally:
        engine.pause();strategy.reset_control_state()
        if stop.is_set() and phase not in ('READY','DONE','ERROR','JOB_FAILED'):
            status('ERROR',message='Operator stopped unfinished motion cycles; no replay')
