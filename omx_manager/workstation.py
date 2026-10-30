"""Persistent multi-pick unloading. One MQTT job may contain many motion cycles."""
import logging
import os
import time
from pathlib import Path

from .basket import BasketInspector,read_camera
from .config import ConfigError, write_json
from .home import errors
from .hooks import heartbeat, initialize_home

logger=logging.getLogger(__name__)


def expected_quantity(items):
    if not isinstance(items,dict): raise ValueError('Unload items must be an object')
    if set(items)-set('ABCDEF'): raise ValueError('Unknown unload item')
    if any(type(v) is not int or not 0<=v<=8 for v in items.values()):
        raise ValueError('Unload quantities must be integers 0..8')
    total=sum(items.values())
    if total>8: raise ValueError('Unload basket capacity is 8')
    return total if items else None


def run(strategy,ctx,send_action,make_timer):
    from lerobot.rollout.workstation_ipc import atomic_json,new_status,read_json
    cfg=strategy._manager_home.cfg
    inspector=BasketInspector(cfg)
    options=inspector.spec
    engine=strategy._engine
    robot=ctx.hardware.robot_wrapper
    inner=getattr(robot,'inner',robot)
    stop=ctx.runtime.shutdown_event
    session=os.environ['OMX_WORKSTATION_SESSION']
    command_path=Path(strategy.config.workstation_control_file)
    status_path=Path(strategy.config.workstation_status_file)
    timer=make_timer(ctx.runtime.cfg.fps,strategy._interpolator.multiplier,records_data=False,report=ctx.runtime.cadence_report)
    phase='READY';job=None;expected=None;attempts=cycles=transfers=no_change=0
    job_started=cycle_started=inspect_started=0.
    baseline=None;after_cycle=False;next_inspect=0.
    basket_path=status_path.with_name('basket_status.json')
    basket_tag=None;next_basket_report=0.

    def basket_report(snapshot):
        nonlocal basket_tag,next_basket_report
        tag=(snapshot['verdict'],snapshot.get('candidate'),snapshot.get('reason'),snapshot.get('camera_state'),snapshot.get('restore_verified'))
        now=time.perf_counter()
        if tag!=basket_tag or now>=next_basket_report:
            write_json(basket_path,snapshot,durable=False)
            basket_tag=tag;next_basket_report=now+.2

    def status(new_phase,**extra):
        nonlocal phase
        phase=new_phase
        row=new_status(session,phase,job,attempts=attempts,completed_cycles=cycles,
                       estimated_transfers=transfers,expected_count=expected,
                       physical_unloading_verified=False,**extra)
        atomic_json(status_path,row)
        # Durable progress survives a killed model process; it never authorizes replay.
        write_json(status_path.with_name('unload_progress.json'),row)
        write_json(basket_path,dict(verdict='UNKNOWN',candidate='UNKNOWN',reason='INSPECTING' if phase=='INSPECTING' else 'POLICY_'+phase,at=time.time()),durable=False)

    def inspect():
        nonlocal inspect_started,next_inspect,baseline
        inspector.reset();inspect_started=time.perf_counter();next_inspect=inspect_started
        status('INSPECTING')

    strategy.reset_control_state()
    status('READY')
    logger.info('[WORKSTATION] READY: multi-cycle unload; learned return region and basket check enabled')
    try:
        while not stop.is_set():
            heartbeat()
            command=read_json(command_path)
            same=command.get('session')==session and command.get('job_id')==job
            if phase=='READY':
                if command.get('session')!=session or command.get('command')!='START' or not command.get('job_id'):
                    stop.wait(strategy.config.workstation_poll_s);continue
                job=str(command['job_id'])
                expected=command.get('expected_count')
                if expected is not None and (type(expected) is not int or not 0<=expected<=8):
                    raise ConfigError('Invalid expected_count in unload command')
                attempts=cycles=transfers=no_change=0
                baseline=None;after_cycle=False
                job_started=time.perf_counter()
                engine.pause();strategy.reset_control_state()
                initialize_home(inner,stopped=stop.is_set)
                inspect();continue
            if phase=='DONE':
                if same and command.get('command')=='CLEAR':
                    strategy.reset_control_state();job=None;status('READY')
                else: stop.wait(strategy.config.workstation_poll_s)
                continue
            now=time.perf_counter()
            if now-job_started>options['job_timeout_s']:
                raise TimeoutError('Unload whole-job timeout; no success claimed')
            if phase=='INSPECTING':
                if now-inspect_started>options['inspect_timeout_s']:
                    raise RuntimeError('Basket view uncertain/stale/occluded; inspection timeout')
                if now<next_inspect:
                    stop.wait(min(.05,next_inspect-now));continue
                next_inspect=now+options['sample_interval_s']
                camera=inner.cameras[options['camera']]
                engine.pause()
                if options['low_exposure_enabled']:
                    from .exposure import inspect as inspect_low
                    def check_home():
                        q=inner.get_joint_observation()
                        if max(errors(q,cfg['home']['target']).values())>cfg['home']['arrival_tolerance']:
                            raise RuntimeError('Arm moved away from inspection Home')
                    verdict=inspect_low(camera,cfg,inspector,guard=check_home,stopped=stop.is_set,report=basket_report)
                    check_home();now=time.perf_counter()
                else:
                    if hasattr(inner,'get_joint_observation') and hasattr(camera,'read_latest_with_timestamp'):
                        obs=inner.get_joint_observation()
                        try:frame,stamp=read_camera(camera,options['max_frame_age_s'])
                        except TimeoutError:
                            inspector.update(None,time.perf_counter(),None)
                            basket_report(inspector.snapshot(time.time()))
                            continue
                        except RuntimeError:
                            inspector.reset()
                            inspector.reason='CAMERA_RECONNECTING'
                            basket_report(inspector.snapshot(time.time()))
                            strategy._get_observation_with_camera_recovery(robot,engine)
                            frame,stamp=read_camera(camera,options['max_frame_age_s'])
                            obs=inner.get_joint_observation()
                        reconnected=False
                    else:
                        obs,reconnected=strategy._get_observation_with_camera_recovery(robot,engine)
                        frame=obs.get(options['camera']);stamp=getattr(camera,'latest_timestamp',None)
                    now=time.perf_counter()
                    if reconnected: inspector.reset()
                    # Inspection only at the strict registered Home; no policy actions here.
                    if max(errors(obs,cfg['home']['target']).values())>cfg['home']['arrival_tolerance']:
                        raise RuntimeError('Arm moved away from basket inspection Home')
                    verdict=inspector.update(frame,now,stamp)
                    basket_report(inspector.snapshot(time.time()))
                if verdict=='UNKNOWN': continue
                if after_cycle:
                    if inspector.transfer_changed(baseline,inspector.latest):
                        transfers+=1;no_change=0
                    else: no_change+=1
                    after_cycle=False
                    logger.info('[UNLOAD] cycles=%s attempts=%s estimated_transfers=%s basket=%s',cycles,attempts,transfers,verdict)
                if verdict=='EMPTY':
                    if options['require_quantity_match'] and expected is not None and transfers!=expected:
                        raise RuntimeError(f'Empty basket but estimated transfer count {transfers} differs from expected {expected}; inspect cargo')
                    status('DONE',reason='basket_empty_confirmed',basket_empty_verified=True,count_is_estimate=True)
                    logger.info('[WORKSTATION] DONE job=%s: basket empty; exact item transfer count remains unverified',job)
                    continue
                if expected is not None and transfers>=expected and options['require_quantity_match']:
                    raise RuntimeError('Expected count reached but basket still occupied; quantity mismatch')
                if attempts>=options['max_attempts'] or no_change>=options['max_no_change']:
                    raise RuntimeError('Unload retry/no-change limit; basket still occupied')
                baseline=inspector.latest.copy()
                attempts+=1
                strategy.reset_control_state();timer.restart()
                strategy._manager_home.reset(now)
                cycle_started=now
                status('RUNNING')
                engine.resume();continue
            if phase!='RUNNING': raise RuntimeError('Unknown unload state: '+phase)
            if now-cycle_started>cfg['runtime']['cycle_timeout_s']:
                raise TimeoutError('Unload single-cycle timeout: meaningful excursion/learned return incomplete')
            timer.tick(new_cycle=strategy._interpolator.needs_new_action())
            obs,reconnected=strategy._get_observation_with_camera_recovery(robot,engine)
            if reconnected:
                strategy._manager_home.reset(time.perf_counter())
                timer.restart()
            now=time.perf_counter()
            if strategy._manager_home.update(obs,now):
                cycles+=1
                engine.pause();strategy.reset_control_state()
                # Only after a complete work cycle may Home correction/inspection begin.
                initialize_home(inner,stopped=stop.is_set)
                after_cycle=True
                inspect();continue
            processed=strategy._process_observation_and_notify(ctx.processors,obs)
            if strategy._handle_warmup(ctx.runtime.cfg.use_torch_compile,timer): continue
            if stop.is_set(): break
            action=send_action(processed,obs,ctx,strategy._interpolator,timer)
            strategy._log_telemetry(processed,action,ctx.runtime)
            engine.pump_query(processed)
            timer.wait()
    except Exception as exc:
        engine.pause()
        status('ERROR',message=str(exc),basket_empty_verified=False)
        raise
    finally:
        engine.pause()
        strategy.reset_control_state()
        if stop.is_set() and phase not in ('READY','DONE','ERROR'):
            status('ERROR',message='Operator stopped unfinished unload; no replay',basket_empty_verified=False)
