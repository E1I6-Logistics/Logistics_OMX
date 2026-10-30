"""Actual managed logic with fake joints, images, clocks and processes only."""
import ast
import copy
import importlib.util
import json
import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from omx_manager import config as C, hooks, runtime as R
from omx_manager.basket import BasketInspector, DEFAULTS, crop, changed_ratio
from omx_manager.home import HomeDetector
from omx_manager.simulation import Clock, FakeFollower
from omx_manager.workflow import StageTracker, registration_binding, require_stages
from omx_manager.return_path import return_home, route_signature, route_entry, segment_error
from omx_manager import workstation as W

ROOT=Path(__file__).resolve().parents[1]


def pose(pan=0.,lift=0.,grip=50.):
    result={j:0. for j in C.JOINTS};result.update(shoulder_pan=float(pan),shoulder_lift=float(lift),gripper=float(grip))
    return result


def enroll(cfg):
    cfg['counting']['mode']='ordered_stages' # exercise the explicit legacy compatibility path
    cfg['home']['target']=pose();cfg['counting']['min_cycle_s']=0
    cfg['workflow']={'items':{item:dict(binding=registration_binding(cfg),stages=[
        dict(name='pick',target=pose(30),tolerance=3.,samples=2),
        dict(name='place',target=pose(-30),tolerance=3.,samples=2)]) for item in C.profile(cfg)['items']}}
    return cfg


@pytest.fixture
def cfg(tmp_path):
    cfg=enroll(C.load(ROOT/'params/omx4.yaml'))
    cfg['unload']=dict(DEFAULTS,roi=[0,0,640,480],illumination_limit=40.,stable_samples=5,sample_interval_s=.2)
    reference=tmp_path/'empty.npz'
    np.savez_compressed(reference,image=np.full((120,160,3),100,np.uint8),roi=cfg['unload']['roi'])
    cfg['unload'].update(reference=str(reference),reference_binding=registration_binding(cfg))
    return cfg


def image(objects=0):
    result=np.full((480,640,3),100,np.uint8)
    if objects>=1: result[100:250,100:220]=0
    if objects>=2: result[100:250,350:470]=0
    return result


def feed(detector,sequence,start=0.,dt=.05):
    completed=[]
    for index,q in enumerate(sequence,1):
        if detector.update(q,start+index*dt): completed.append(index)
    return completed


def test_glancing_home_never_counts_without_ordered_work_stages(cfg,monkeypatch,tmp_path):
    monkeypatch.setattr(hooks,'ROOT',tmp_path)
    detector=hooks.RolloutHome(cfg,0)
    assert not feed(detector,[pose(13)]+[pose(6)]*40)
    assert not detector.tracker.complete
    assert not feed(detector,[pose(0,0,80)]+[pose()]*40,start=3)


def test_registered_stages_must_be_in_order_and_not_just_any_motion(cfg,monkeypatch,tmp_path):
    monkeypatch.setattr(hooks,'ROOT',tmp_path)
    detector=hooks.RolloutHome(cfg,0)
    assert not feed(detector,[pose(-30)]*3+[pose(30)]*3+[pose()]*30)
    detector.reset(3)
    assert len(feed(detector,[pose(30)]*3+[pose(-30)]*3+[pose()]*30,start=3))==1
    data=json.loads((tmp_path/'state/manager/omx4/joint_errors.json').read_text())
    # The last completion snapshot may be followed by an idle sample: capture at event below.
    assert data['physical_transfer_verified'] is False


def test_completion_snapshot_preserves_pre_reset_evidence(cfg,monkeypatch):
    rows=[];monkeypatch.setattr(hooks,'write_json',lambda path,row,**kw:rows.append(row) if path.name=='joint_errors.json' else None)
    detector=hooks.RolloutHome(cfg,0)
    feed(detector,[pose(30)]*3+[pose(-30)]*3+[pose()]*30)
    done=[r for r in rows if r['completed']]
    assert len(done)==1
    assert done[0]['departed'] and done[0]['still'] and done[0]['in_home_window']
    assert done[0]['hold_since'] is not None and done[0]['sample_gap_s']<=.2
    assert done[0]['stage_index']==done[0]['stage_total']==2


def test_stage_gap_does_not_satisfy_consecutive_samples(cfg):
    tracker=StageTracker(require_stages(cfg,'F'),.2)
    tracker.update(pose(30),.1);tracker.update(pose(30),2.1)
    assert tracker.index==0
    tracker.update(pose(30),2.15)
    assert tracker.index==1


@pytest.mark.parametrize('bad', ['missing','home','overlap','binding'])
def test_unregistered_or_ambiguous_stages_fail_closed(cfg,bad):
    row=cfg['workflow']['items']['F']
    if bad=='missing': row['stages']=[]
    if bad=='home': row['stages'][0]['target']=pose()
    if bad=='overlap': row['stages'][1]['target']=pose(31)
    if bad=='binding': cfg['home']['target']=pose(1)
    with pytest.raises(C.ConfigError): require_stages(cfg,'F')


@pytest.mark.parametrize('objects,expected',[(0,'EMPTY'),(1,'OCCUPIED'),(2,'OCCUPIED')])
def test_basket_requires_fresh_stable_frames(cfg,objects,expected):
    inspector=BasketInspector(cfg)
    for i in range(5):
        result=inspector.update(image(objects),1+i*.21,1+i*.21)
        assert result==('UNKNOWN' if i<4 else expected)


@pytest.mark.parametrize('kind',['duplicate','stale','light','unstable','uncertain'])
def test_basket_bad_view_never_confirms_empty(cfg,kind):
    inspector=BasketInspector(cfg)
    for i in range(12):
        now=1+i*.21;frame=image()
        stamp=now
        if kind=='duplicate': stamp=1.
        if kind=='stale': stamp=now-1
        if kind=='light': frame[:]=240
        if kind=='unstable': frame[:]=100 if i%2 else 105
        if kind=='uncertain': frame[10:100,10:100]=0 # between .01 and .04
        assert inspector.update(frame,now,stamp)=='UNKNOWN'


def test_basket_image_check_does_not_mutate_policy_images(cfg):
    source=image(2);before=source.copy()
    small=crop(source,cfg['unload'])
    assert small.shape==(120,160,3)
    np.testing.assert_array_equal(source,before)
    assert changed_ratio(small,small,25)==0


@pytest.mark.parametrize('change',['home','roi','file'])
def test_reference_binding_and_roi_are_checked(cfg,change):
    if change=='home': cfg['home']['target']=pose(1)
    if change=='roi': cfg['unload']['roi']=[0,0,600,400]
    if change=='file': cfg['unload']['reference']='missing-reference.npz'
    with pytest.raises((C.ConfigError,OSError)): BasketInspector(cfg)


def route_cfg(cfg):
    cfg=copy.deepcopy(cfg)
    cfg['recovery']['routes']={'fold':dict(points=[pose(60,40),pose(30,20),pose()],radius=1.,approved=None)}
    route=cfg['recovery']['routes']['fold'];route['approved']=route_signature(cfg,route)
    return cfg,route


def test_registered_return_uses_only_approved_segments_and_bounded_steps(cfg):
    cfg,route=route_cfg(cfg);clock=Clock();device=FakeFollower(pose(60,40))
    result=return_home(device,cfg,clock=clock,sleep=clock.sleep)
    assert max(abs(result[j]-pose()[j]) for j in C.JOINTS)<=cfg['home']['arrival_tolerance']
    previous=pose(60,40)
    for command in device.sent:
        assert max(abs(command[j]-previous[j]) for j in C.JOINTS)<=cfg['home']['max_step']+1e-8
        assert min(segment_error(command,a,b)[0] for a,b in zip(route['points'],route['points'][1:]))<=1.+1e-8
        previous=command


@pytest.mark.parametrize('kind',['unknown','unapproved','changed_home','operator_stop','hardware_fault'])
def test_unsafe_return_never_sends_a_goal(cfg,kind):
    cfg,route=route_cfg(cfg);clock=Clock();device=FakeFollower(pose(60,40))
    if kind=='unknown': device.q=pose(-60,50)
    if kind=='unapproved': route['approved']=None
    if kind=='changed_home': cfg['home']['target']=pose(1)
    if kind=='hardware_fault': device.read=Mock(side_effect=RuntimeError('hardware error'))
    with pytest.raises((C.ConfigError,InterruptedError,RuntimeError)):
        return_home(device,cfg,clock=clock,sleep=clock.sleep,stopped=lambda:kind=='operator_stop')
    assert device.sent==[]


def test_route_test_requires_entire_route_from_first_waypoint(cfg):
    cfg,route=route_cfg(cfg)
    with pytest.raises(C.ConfigError,match='first waypoint'): route_entry(cfg,route,pose(30,20),test=True)
    assert route_entry(cfg,route,pose(60,40),test=True)==0


def test_frozen_return_is_bounded_and_never_marks_success(cfg):
    cfg,route=route_cfg(cfg);clock=Clock();device=FakeFollower(pose(60,40))
    device.send=lambda command:device.sent.append(command.copy())
    with pytest.raises(TimeoutError): return_home(device,cfg,clock=clock,sleep=clock.sleep)
    assert max(max(abs(cmd[j]-device.q[j]) for j in C.JOINTS) for cmd in device.sent)<=1.+1e-8


def test_error_return_preserves_gripper_even_on_tested_route(cfg):
    cfg,route=route_cfg(cfg);clock=Clock();device=FakeFollower(pose(60,40,80))
    result=return_home(device,cfg,clock=clock,sleep=clock.sleep,preserve_gripper=True)
    assert result['gripper']==80
    assert all(command['gripper']==80 for command in device.sent)
    assert abs(result['shoulder_pan'])<=.8 and abs(result['shoulder_lift'])<=.8
    assert cfg['home']['target']['gripper']==50
    assert route['points'][0]['gripper']==50


class Stop:
    def __init__(self,clock,limit=100):self.clock=clock;self.stop=False;self.limit=limit
    def is_set(self):return self.stop or self.clock()>self.limit
    def set(self):self.stop=True
    def wait(self,dt):self.clock.sleep(dt);return self.is_set()


def controller(cfg,tmp_path,monkeypatch,*,count=2,expected=2,no_change=False,wrong_session=False,stage_failure=False,stale=False,jobs=1,paired=False,controls=None):
    # Load only the real IPC file, never the LeRobot torch/model package.
    ipc_path=ROOT/'lerobot_unified/src/lerobot/rollout/workstation_ipc.py'
    spec=importlib.util.spec_from_file_location('lerobot.rollout.workstation_ipc',ipc_path)
    ipc=importlib.util.module_from_spec(spec);spec.loader.exec_module(ipc)
    monkeypatch.setitem(sys.modules,'lerobot.rollout.workstation_ipc',ipc)
    clock=Clock();stop=Stop(clock,60);status_path=tmp_path/'status.json';command_path=tmp_path/'command.json'
    C.write_json(command_path,dict(session='wrong' if wrong_session else 'test',command='START',job_id='job',expected_count=expected))
    monkeypatch.setenv('OMX_WORKSTATION_SESSION','test')
    monkeypatch.setattr(W,'time',NS(perf_counter=clock,time=lambda:1000+clock()))
    monkeypatch.setattr(W,'heartbeat',lambda:None)
    monkeypatch.setattr(hooks,'ROOT',tmp_path)
    phases=[];run_samples={};sent=[];done_rows=[]
    inner=NS(q=pose(),cameras={'top':NS(latest_timestamp=None)})
    if controls is not None:inner.cameras['top']._omx_test_controls=controls
    def home(robot,stopped):
        if stopped():raise InterruptedError()
        robot.q=pose()
    monkeypatch.setattr(W,'initialize_home',home)
    original=ipc.atomic_json
    def write_status(path,row):
        phases.append(row['phase']);original(path,row)
        if row['phase']=='DONE':
            done_rows.append(row.copy())
            if len(done_rows)>=jobs: stop.set()
            else: C.write_json(command_path,dict(session='test',command='CLEAR',job_id=row['job_id']))
        if row['phase']=='READY' and done_rows and len(done_rows)<jobs:
            C.write_json(command_path,dict(session='test',command='START',job_id='job'+str(len(done_rows)+1),expected_count=expected))
    monkeypatch.setattr(ipc,'atomic_json',write_status)
    def observe(*_):
        row=ipc.read_json(status_path)
        completed=row.get('completed_cycles',0)
        if row.get('phase')=='RUNNING':
            attempt=(row['job_id'],row['attempts']);n=run_samples.get(attempt,0);run_samples[attempt]=n+1
            if cfg['counting'].get('mode')=='cycle':inner.q=pose(30) if n<20 else pose()
            else:inner.q=pose(20) if stage_failure else pose(30) if n<3 else pose(-30) if n<6 else pose()
        frame=image(count if no_change else max(0,count-completed))
        inner.cameras['top'].latest_timestamp=clock()-1 if stale else clock()
        return dict(inner.q,top=frame),False
    class Timer:
        def __init__(self,*a,**kw):pass
        def tick(self,**kw):pass
        def restart(self):pass
        def wait(self):clock.sleep(.05)
    engine=Mock();strategy=NS(_manager_home=hooks.RolloutHome(cfg,0),_engine=engine,
        _interpolator=NS(multiplier=1,needs_new_action=lambda:True),
        config=NS(workstation_control_file=str(command_path),workstation_status_file=str(status_path),workstation_poll_s=.1),
        reset_control_state=Mock(),_get_observation_with_camera_recovery=observe,
        _process_observation_and_notify=lambda p,o:o,_handle_warmup=lambda *_:False,_log_telemetry=Mock())
    if paired:
        from omx_manager import basket
        monkeypatch.setattr(basket,'time',NS(perf_counter=clock))
        inner.get_joint_observation=lambda:inner.q.copy()
        def camera_read(**kwargs):
            row=ipc.read_json(status_path)
            inner.cameras['top'].latest_timestamp=clock()
            return image(max(0,count-row.get('completed_cycles',0))),clock()
        inner.cameras['top'].read_latest_with_timestamp=camera_read
    ctx=NS(runtime=NS(cfg=NS(fps=30,use_torch_compile=False),shutdown_event=stop,cadence_report=None),
           hardware=NS(robot_wrapper=NS(inner=inner)),processors=None)
    def send(*args):
        if controls is not None:
            from omx_manager.exposure import ensure_policy_ready
            ensure_policy_ready(inner)
        assert ipc.read_json(status_path)['phase']=='RUNNING'
        sent.append(inner.q.copy())
        strategy._manager_home.note_action()
    return lambda:W.run(strategy,ctx,send,Timer),phases,sent,done_rows,status_path


def test_actual_unload_repeats_until_two_objects_are_removed(cfg,tmp_path,monkeypatch):
    run,phases,sent,done,path=controller(cfg,tmp_path,monkeypatch)
    run()
    assert len(done)==1 and done[0]['basket_empty_verified']
    assert done[0]['completed_cycles']==done[0]['estimated_transfers']==2
    assert done[0]['physical_unloading_verified'] is False
    assert phases.count('RUNNING')==2 and sent
    assert json.loads(path.with_name('unload_progress.json').read_text())['phase']=='DONE'


def test_until_empty_with_no_expected_quantity_still_requires_visual_empty(cfg,tmp_path,monkeypatch):
    run,phases,sent,done,path=controller(cfg,tmp_path,monkeypatch,expected=None)
    run()
    assert len(done)==1 and done[0]['expected_count'] is None
    assert done[0]['basket_empty_verified'] and done[0]['completed_cycles']==2


def test_two_consecutive_orders_reset_act_and_counters_without_reloading(cfg,tmp_path,monkeypatch):
    run,phases,sent,done,path=controller(cfg,tmp_path,monkeypatch,jobs=2)
    run()
    assert [r['job_id'] for r in done]==['job','job2']
    assert [r['estimated_transfers'] for r in done]==[2,2]
    assert phases.count('RUNNING')==4 and phases.count('READY')==2


@pytest.mark.parametrize('limit',['attempts','job'])
def test_unload_global_attempt_and_job_limits(cfg,tmp_path,monkeypatch,limit):
    if limit=='attempts':cfg['unload']['max_attempts']=1
    else:cfg['unload']['job_timeout_s']=1
    run,phases,sent,done,path=controller(cfg,tmp_path,monkeypatch,expected=None)
    with pytest.raises((RuntimeError,TimeoutError)):run()
    assert not done and phases[-1]=='ERROR'


@pytest.mark.parametrize('failure',['no_change','stage_timeout','quantity','stale'])
def test_unload_failure_never_reports_done(cfg,tmp_path,monkeypatch,failure):
    cfg['runtime']['cycle_timeout_s']=2.
    run,phases,sent,done,path=controller(cfg,tmp_path,monkeypatch,no_change=failure=='no_change',
        stage_failure=failure=='stage_timeout',expected=3 if failure=='quantity' else 2,stale=failure=='stale')
    with pytest.raises((RuntimeError,TimeoutError)):run()
    assert not done and phases[-1]=='ERROR'
    if failure=='stale':assert not sent


def test_stale_session_never_starts_unload(cfg,tmp_path,monkeypatch):
    run,phases,sent,done,_=controller(cfg,tmp_path,monkeypatch,wrong_session=True)
    run()
    assert phases==['READY'] and not sent and not done


@pytest.mark.parametrize('items,expected',[({'A':2,'D':1},3),({},None),({'F':0},0)])
def test_expected_count_is_carried_as_quantity_not_trigger(items,expected):
    assert W.expected_quantity(items)==expected


@pytest.mark.parametrize('items',[{'F':True},{'F':-1},{'F':1.5},{'G':1},{'A':8,'B':1},[]])
def test_bad_unload_quantities_are_rejected(items):
    with pytest.raises(ValueError):W.expected_quantity(items)


def test_lightweight_wait_rejects_jobs_without_importing_torch():
    from omx_manager.recovery_wait import rejected_ack
    assert rejected_ack('omx4',{'job_id':'x'})==('omx4/ack',dict(job_id='x',accepted=False,reason='RECOVERY_WAIT'))
    assert rejected_ack('omx4',{'job_id':False}) is None
    source=(ROOT/'omx_manager/recovery_wait.py').read_text()
    assert 'import torch' not in source and 'FollowerBus' not in source


def test_supervisor_wait_keeps_only_lightweight_client_and_stops_cleanly(cfg,tmp_path,monkeypatch):
    from omx_manager import commissioning
    path=tmp_path/'cfg.yaml';C.save(path,cfg);supervisor=R.Supervisor(path)
    monkeypatch.setattr(R,'state_dir',lambda _:tmp_path/'runtime')
    monkeypatch.setattr(R,'check_files',lambda _:None)
    monkeypatch.setattr(commissioning,'require_commissioned',lambda _:None)
    commands=[];closed=[]
    monkeypatch.setattr(R.subprocess,'Popen',lambda argv,**kw:commands.append(argv) or NS(pid=123,poll=lambda:None))
    monkeypatch.setattr(R,'stop_process',lambda proc,timeout:closed.append(proc.pid))
    class Event:
        count=0
        def is_set(self):return self.count>=3
        def wait(self,dt):self.count+=1;return self.is_set()
    supervisor.stopped=Event()
    supervisor.preflight=Mock(side_effect=R.OperatorRecoveryRequired('unknown pose'))
    supervisor.wait_for_recovery(cfg,R.OperatorRecoveryRequired('unknown pose'))
    assert len(commands)==1 and 'omx_manager.recovery_wait' in commands[0]
    assert closed==[123]
    assert all(call.kwargs.get('allow_motion') is False for call in supervisor.preflight.call_args_list)
    row=json.loads((tmp_path/'runtime/recovery_wait.json').read_text())
    assert row['phase']=='RECOVERY_WAIT' and row['model_loaded'] is False


def test_generic_usb_failure_does_not_trigger_automatic_model_reload(cfg,tmp_path,monkeypatch):
    from omx_manager import commissioning
    path=tmp_path/'cfg.yaml';C.save(path,cfg);supervisor=R.Supervisor(path)
    monkeypatch.setattr(R,'state_dir',lambda _:tmp_path/'runtime')
    monkeypatch.setattr(R,'check_files',lambda _:None)
    monkeypatch.setattr(commissioning,'require_commissioned',lambda _:None)
    monkeypatch.setattr(R.subprocess,'Popen',lambda *a,**kw:NS(pid=123,poll=lambda:None))
    monkeypatch.setattr(R,'stop_process',lambda *a:None)
    class Event:
        count=0
        def is_set(self):return self.count>=4
        def wait(self,dt):self.count+=1;return self.is_set()
    supervisor.stopped=Event();supervisor.preflight=Mock()
    supervisor.wait_for_recovery(cfg,RuntimeError('USB camera failed'))
    supervisor.preflight.assert_not_called()


def test_item_switch_uses_external_omx2_stage_names(cfg,monkeypatch):
    cfg=enroll(C.load(ROOT/'params/omx2.yaml'))
    detector=hooks.RolloutHome(cfg,0)
    detector.select_item('B',1)
    assert detector.item=='D'
    detector.select_item('A',2)
    assert detector.item=='C'


@pytest.mark.parametrize('key,value',[('roi',[-1,0,20,20]),('roi',[0,0,999,20]),('stable_samples',True),('empty_ratio',.08),('sample_interval_s',0),('require_quantity_match','yes')])
def test_bad_unload_parameters_are_rejected(cfg,key,value):
    cfg['unload'][key]=value
    with pytest.raises(C.ConfigError):C.validate(cfg)


def test_actual_mqtt_unload_passes_quantities_and_requires_empty_confirmation(monkeypatch,tmp_path):
    tree=ast.parse((ROOT/'lerobot_unified/omx_mqtt_client.py').read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='execute_unload')
    import os
    monkeypatch.setenv('OMX_MANAGED_UNLOAD','1')
    current={'phase':'READY'};commands=[];messages=[];reads=0
    def write(path,row):
        commands.append(row)
        current['phase']='RUNNING' if row['command']=='START' else 'READY'
    def state():
        nonlocal reads
        reads+=1
        row=dict(session='test',phase='RUNNING' if reads==1 else 'DONE',job_id='j',
                 reason='basket_empty_confirmed',basket_empty_verified=True,estimated_transfers=2)
        return row
    clock=Clock()
    ns=dict(robot_id='omx4',WORKSTATION_IDS=('omx3','omx4'),workstation_ready=lambda:current['phase']=='READY',
            workstation_session='test',workstation_command=tmp_path/'cmd',atomic_json=write,
            workstation_state=state,_workstation_process_alive=lambda _:True,os=os,
            time=NS(time=clock,monotonic=clock,sleep=clock.sleep),publish=lambda topic,payload:messages.append((topic,payload)))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'real mqtt unload','exec'),ns)
    result=ns['execute_unload']('j',{'A':1,'D':1})
    assert commands[0]['expected_count']==2 and commands[0]['items']=={'A':1,'D':1}
    assert [r['command'] for r in commands]==['START','CLEAR']
    assert result['reason']=='basket_empty_confirmed' and messages[0][1]['count_is_estimate']


def test_shutdown_during_real_action_helper_inference_never_sends(monkeypatch):
    import contextlib,os
    path=ROOT/'lerobot_unified/src/lerobot/rollout/strategies/core.py'
    tree=ast.parse(path.read_text())
    fn=copy.deepcopy(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='send_next_action'))
    fn.returns=None
    for arg in fn.args.args:arg.annotation=None
    stop=threading.Event();engine=Mock();interp=Mock()
    engine.get_action.side_effect=lambda _:stop.set() or NS(cpu=lambda:np.zeros(6))
    interp.needs_new_action.return_value=True;interp.get.return_value=np.zeros(6)
    wrapper=Mock()
    ctx=NS(policy=NS(inference=engine),data=NS(dataset_features={},ordered_action_keys=list(C.JOINTS)),
           runtime=NS(shutdown_event=stop),hardware=NS(robot_wrapper=wrapper),processors=NS(robot_action_processor=lambda x:x[0]))
    ns=dict(os=os,Path=Path,contextlib=contextlib,OBS_STR='observation',build_dataset_frame=lambda *a,**kw:{})
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'real dispatcher','exec'),ns)
    with pytest.raises(InterruptedError):ns['send_next_action']({}, {},ctx,interp)
    wrapper.send_action.assert_not_called()


def test_actual_route_cli_rehearses_whole_path_before_approval(cfg,tmp_path,monkeypatch):
    from omx_manager import cli
    from omx_manager.hardware import Lock
    cfg,route=route_cfg(cfg);route['approved']=None
    path=tmp_path/'cfg.yaml';C.save(path,cfg)
    clock=Clock();device=FakeFollower(pose());device.bus=NS(read=lambda key,j:5 if j=='gripper' else 4)
    class Bus:
        def __init__(self,_):pass
        def __enter__(self):return device
        def __exit__(self,*_):device.closed=True
    monkeypatch.setattr(cli,'FollowerBus',Bus)
    monkeypatch.setattr(cli,'require_home',lambda _:None)
    monkeypatch.setattr(cli,'device_lock',lambda _:Lock(tmp_path/'device.lock'))
    monkeypatch.setattr(cli.time,'monotonic',clock);monkeypatch.setattr(cli.time,'sleep',clock.sleep)
    monkeypatch.setattr(hooks,'ROOT',tmp_path)
    cli.invoke(path,'route-test',confirm_motion=True,name='fold',round_trip=True)
    after=C.load(path)
    assert max(cmd['shoulder_pan'] for cmd in device.sent)>59
    assert abs(device.q['shoulder_pan'])<=.8 and device.closed
    assert after['recovery']['routes']['fold']['approved']==route_signature(after,after['recovery']['routes']['fold'])


def test_route_cli_refuses_unconfirmed_motion_before_opening_port(cfg,tmp_path,monkeypatch):
    from omx_manager import cli
    path=tmp_path/'cfg.yaml';C.save(path,cfg)
    connect=Mock(side_effect=AssertionError('No port access for unconfirmed motion'))
    monkeypatch.setattr(cli,'FollowerBus',connect)
    with pytest.raises(ValueError,match='confirm-motion'):
        cli.invoke(path,'route-test',name='fold',round_trip=True)
    connect.assert_not_called()


@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
def test_seeded_twenty_stage_cycles_per_device(device,monkeypatch):
    import random,os
    rng=random.Random(int(os.environ.get('OMX_TEST_SEED','1'))+int(device[-1]))
    cfg=enroll(C.load(ROOT/f'params/{device}.yaml'))
    monkeypatch.setattr(hooks,'write_json',lambda *a,**kw:None)
    manager=hooks.RolloutHome(cfg,0);now=0.;total=0
    for cycle in range(20):
        for base in [pose(13)]+[pose()]*15:
            now+=.05
            assert not manager.update(base,now) # partial departure is not work
        for index,base in enumerate([pose(30)]*3+[pose(-30)]*3+[pose()]*20):
            if index==6 and cycle%3==0:now+=2 # a gap never contributes observed hold
            now+=.05
            noisy={j:v+rng.uniform(-.02,.02) for j,v in base.items()}
            total+=manager.update(noisy,now)
    assert total==20


def test_manual_until_empty_order_has_no_invented_quantity(cfg,monkeypatch):
    from omx_manager import orders
    publish=Mock();monkeypatch.setattr(orders.shutil,'which',lambda _:'/fake/mosquitto_pub')
    monkeypatch.setattr(orders.subprocess,'run',publish)
    assert orders.send_order(cfg,until_empty=True)['items']=={}
    publish.assert_called_once()
    cfg['role']='pickup'
    with pytest.raises(ValueError):orders.send_order(cfg,until_empty=True)


@pytest.mark.parametrize('torque',[0,1])
def test_actual_preflight_never_enables_torque_or_opens_unknown_cargo(cfg,tmp_path,monkeypatch,torque):
    from omx_manager import hardware
    clock=Clock();device=FakeFollower(pose(grip=60))
    device.bus=NS(read=lambda key,j:torque if key=='Torque_Enable' else 5 if j=='gripper' else 4)
    class Bus:
        def __init__(self,_):pass
        def __enter__(self):return device
        def __exit__(self,*_):pass
    monkeypatch.setattr(hardware,'FollowerBus',Bus)
    monkeypatch.setattr(R,'state_dir',lambda _:tmp_path/'runtime')
    monkeypatch.setattr(R.time,'monotonic',clock);monkeypatch.setattr(R.time,'sleep',clock.sleep)
    monkeypatch.setattr(hooks,'ROOT',tmp_path)
    supervisor=R.Supervisor(tmp_path/'cfg.yaml')
    with pytest.raises(R.OperatorRecoveryRequired):supervisor.preflight(cfg,verify_registration=False)
    assert all(cmd['gripper']==60 for cmd in device.sent)
    if torque==0:assert not device.sent
    else:
        supervisor.preflight(cfg,verify_registration=False,operator_confirmed=True)
        assert abs(device.q['gripper']-50)<=.8
