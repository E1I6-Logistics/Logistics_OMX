"""Hardware-free learned cycles, CPU/queue behavior and fresh camera decisions."""
import ast
import contextlib
import copy
from collections import deque
import importlib.util
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from omx_manager import config as C,hooks,cpu,commissioning
from omx_manager.cycles import CycleDetector,ARM,PRIMARY,learn_profile,require_profile
from omx_manager.simulation import Clock
from omx_manager.basket import BasketInspector,DEFAULTS,read_camera

ROOT=Path(__file__).resolve().parents[1]


def pose(pan=0,wrist=0,grip=50):
    q={j:0. for j in C.JOINTS};q.update(shoulder_pan=float(pan),wrist_flex=float(wrist),gripper=float(grip));return q


def examples(wrist=-10):
    return [dict(confirmed=True,actions=120,duration_s=12,
        trajectory=[pose(),pose(30+i*10),pose(-20),pose(wrist=wrist+i*.1)],
        return_samples=[pose(wrist=wrist+i*.1)]*5) for i in range(3)]


def trained(device='omx1',wrist=-10):
    cfg=C.load(ROOT/f'params/{device}.yaml');cfg['home']['target']=pose()
    cfg['counting']['mode']='cycle';cfg['counting']['profiles']={}
    for item in C.profile(cfg)['items']:cfg['counting']['profiles'][item]=learn_profile(cfg,item,examples(wrist))
    return cfg


@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
def test_all_arms_count_eight_pick_locations_without_pick_place_registration(device):
    cfg=trained(device);item=next(iter(C.profile(cfg)['items']))
    cfg['workflow']={'items':{}}
    detector=CycleDetector(cfg,item);now=0.;count=0
    for location in range(8):
        for q in [pose(20+location*5)]*20+[pose(wrist=-10.11,grip=80)]*130:
            now+=.05
            count+=detector.update(q,now)
            detector.note_action()
    assert count==8
    assert abs(-10.11-cfg['home']['target']['wrist_flex'])>cfg['home']['tolerance']['wrist_flex']
    assert cfg['home']['target']==pose() # fixed start Home is NOT overwritten


@pytest.mark.parametrize('bad',['idle','gripper','wrist','brief_exit','no_actions','gap'])
def test_cycle_jitter_and_unobserved_holds_never_count(bad):
    cfg=trained();d=CycleDetector(cfg,'A');now=0.;count=0
    for i in range(180):
        q=pose(wrist=-10.1)
        if bad=='gripper':q['gripper']=0 if i%2 else 100
        if bad=='wrist':q['wrist_roll']=40 if i<20 else 0
        if bad=='brief_exit' and i==0:q['shoulder_pan']=30
        if bad in ('no_actions','gap') and i<30:q['shoulder_pan']=30
        now+=.5 if bad=='gap' else .05
        count+=d.update(q,now)
        if bad!='no_actions':d.note_action()
    assert count==0


def test_return_gap_does_not_contribute_to_hold():
    cfg=trained();d=CycleDetector(cfg,'A');now=0
    for _ in range(110):
        now+=.05;d.update(pose(30),now);d.note_action()
    d.update(pose(wrist=-10.1),now+.05)
    assert not d.update(pose(wrist=-10.1),now+2.05)
    assert d.since is None
    assert sum(d.update(pose(wrist=-10.1),now+2.05+i*.05) for i in range(1,18))==1


def test_missing_or_changed_profile_never_falls_back_to_old_pick_stage():
    cfg=trained();cfg['counting']['profiles'].pop('A')
    with pytest.raises(C.ConfigError,match='omx_cycle_calibrate'):require_profile(cfg,'A')
    cfg=trained();cfg['home']['target']['wrist_flex']=1
    with pytest.raises(C.ConfigError,match='changed'):require_profile(cfg,'A')


@pytest.mark.parametrize('bad',['unconfirmed','short','no_motion','far_return','wide_returns'])
def test_learning_rejects_bad_examples(bad):
    cfg=trained();captures=examples()
    if bad=='unconfirmed':captures[0]['confirmed']=False
    if bad=='short':captures[0]['actions']=3
    if bad=='no_motion':captures[0]['trajectory']=[pose()]*10
    if bad=='far_return':captures[0]['return_samples']=[pose(50)]*5
    if bad=='wide_returns':captures[0]['return_samples']=[pose(wrist=5)]*5
    with pytest.raises(C.ConfigError):learn_profile(cfg,'A',captures)


def test_one_shot_diagnostics_preserve_cycle_evidence(monkeypatch):
    cfg=trained();rows=[]
    monkeypatch.setattr(hooks,'write_json',lambda path,row,**kw:rows.append((path.name,row)))
    m=hooks.RolloutHome(cfg,0);now=0
    for q in [pose(30)]*30+[pose(wrist=-10.1,grip=90)]*150:
        now+=.05;m.update(q,now);m.note_action()
    cycles=[r for name,r in rows if name=='last_cycle.json']
    assert len(cycles)==1 and cycles[0]['counter_mode']=='cycle'
    assert cycles[0]['actions']>=30 and cycles[0]['joints']['gripper']['ignored']
    assert cycles[0]['stage_index'] is None and cycles[0]['phase']=='CYCLE_COMPLETED'


@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
def test_tune_sets_four_threads_and_preserves_models_home_and_old_reference(device):
    cfg=C.load(ROOT/f'params/{device}.yaml')
    if device in ('omx3','omx4'):cfg['unload']=dict(DEFAULTS,reference='my-local-reference.npz')
    after=commissioning.tune(cfg)
    assert after['runtime']['cpu_threads']==4 and after['counting']['mode']=='cycle'
    for key in ('hardware','home','models'):assert after[key]==cfg[key]
    if device in ('omx3','omx4'):
        assert after['unload']['reference']=='my-local-reference.npz'
        assert after['unload']['sample_interval_s']==.1 and after['unload']['stable_samples']==3
    C.validate(after)


def test_explicit_torch_cpu_pool_and_interop_limit(monkeypatch):
    monkeypatch.setenv('OMX_MANAGER_CONFIG','local.yaml');monkeypatch.setenv('OMP_NUM_THREADS','4')
    torch=Mock();torch.get_num_threads.return_value=4;torch.get_num_interop_threads.return_value=1
    assert cpu.configure_cpu(torch)==dict(intra_op=4,inter_op=1)
    torch.set_num_threads.assert_called_once_with(4);torch.set_num_interop_threads.assert_called_once_with(1)


def method(path,name,cls=None):
    tree=ast.parse((ROOT/path).read_text())
    nodes=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls).body if cls else tree.body
    fn=copy.deepcopy(next(n for n in nodes if isinstance(n,ast.FunctionDef) and n.name==name))
    fn.decorator_list=[];fn.returns=None
    for arg in fn.args.args:arg.annotation=None
    return fn


class Tensor:
    def __init__(self,values):self.values=np.asarray(values)
    def squeeze(self,*a):return self
    def cpu(self):return self


@pytest.mark.parametrize('fast,ensemble,robot',[(True,None,'omx_follower'),(False,None,'omx_follower'),(True,.01,'omx_follower'),(True,None,'other')])
def test_actual_act_queue_path_skips_only_redundant_preprocessing(monkeypatch,fast,ensemble,robot):
    fn=method('lerobot_unified/src/lerobot/rollout/inference/sync.py','get_action','SyncInferenceEngine')
    class ACTPolicy:
        config=NS(type='act',temporal_ensemble_coeff=ensemble)
        def __init__(self):self._action_queue=deque()
        def drop_queued_actions(self):self._action_queue.clear()
        def select_action(self,batch):
            if not self._action_queue:self._action_queue.extend([Tensor([1,2]),Tensor([3,4])])
            return self._action_queue.popleft()
    pre=Mock(side_effect=lambda x:x);post=Mock(side_effect=lambda x:x)
    engine=NS(_policy=ACTPolicy(),_preprocessor=pre,_postprocessor=post,_device=NS(type='cpu'),
        _robot_type=robot,_dataset_features={},_ordered_action_keys=['b','a'],_take_task=lambda:('task',False),_set_dispatched_task=Mock())
    fake_torch=NS(inference_mode=contextlib.nullcontext,tensor=lambda x:x)
    ns=dict(torch=fake_torch,os=os,copy=copy.copy,nullcontext=contextlib.nullcontext,
            prepare_observation_for_inference=lambda x,*a:x,make_robot_action=lambda x,*a:dict(a=x.values[0],b=x.values[1]),logger=logging.getLogger('test'))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'real sync','exec'),ns)
    monkeypatch.setenv('OMX_ACT_FAST_QUEUE','1' if fast else '0')
    assert ns['get_action'](engine,{'camera':'frame'})==[2,1]
    assert ns['get_action'](engine,{'camera':'frame'})==[4,3]
    assert pre.call_count==(1 if fast and ensemble is None and robot=='omx_follower' else 2)
    assert post.call_count==2


@pytest.fixture
def basket_cfg(tmp_path):
    from omx_manager.workflow import registration_binding
    cfg=trained('omx4');cfg['unload']=dict(DEFAULTS,roi=[0,0,640,480],illumination_limit=40)
    path=tmp_path/'empty.npz';np.savez_compressed(path,image=np.full((120,160,3),100,np.uint8),roi=cfg['unload']['roi'])
    cfg['unload'].update(reference=str(path),reference_binding=registration_binding(cfg))
    return cfg


def frame(occupied=False):
    image=np.full((480,640,3),100,np.uint8)
    if occupied:image[100:250,100:250]=0
    return image


def test_presence_to_empty_clears_old_label_immediately_and_confirms_in_point_two_seconds(basket_cfg):
    inspector=BasketInspector(basket_cfg)
    for t in (1.,1.1,1.21):result=inspector.update(frame(True),t,t)
    assert result=='OCCUPIED'
    assert inspector.update(frame(False),1.32,1.32)=='UNKNOWN'
    assert inspector.result=='UNKNOWN' and inspector.verdict=='EMPTY'
    assert inspector.update(frame(False),1.43,1.43)=='UNKNOWN'
    assert inspector.update(frame(False),1.54,1.54)=='EMPTY'
    assert inspector.processing_ms>=0 and inspector.frame_age_ms==0
    assert inspector.update(None,2.,None)=='UNKNOWN' and inspector.snapshot(2.)['candidate']=='UNKNOWN'


def test_duplicate_frames_do_not_accumulate_confirmation(basket_cfg):
    inspector=BasketInspector(basket_cfg)
    assert inspector.update(frame(),1.,1.)=='UNKNOWN'
    for t in (1.05,1.1,1.15,1.2):assert inspector.update(frame(),t,1.)=='UNKNOWN'
    assert inspector.samples==1


@pytest.mark.parametrize('backend',['opencv','realsense'])
def test_actual_driver_returns_atomic_frame_timestamp_and_refuses_stale(backend):
    if backend=='opencv':
        fn=method('lerobot_unified/src/lerobot/cameras/opencv/camera_opencv.py','read_latest_with_timestamp','OpenCVCamera')
    else:fn=method('lerobot_unified/src/lerobot/cameras/realsense/camera_realsense.py','_latest_snapshot','RealSenseCamera')
    image=frame();camera=NS(thread=NS(is_alive=lambda:True),frame_lock=contextlib.nullcontext(),latest_frame=image,latest_color_frame=image,latest_timestamp=.99)
    ns=dict(time=NS(perf_counter=lambda:1.))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'actual camera snapshot','exec'),ns)
    returned,stamp=ns[fn.name](camera,250)
    assert returned is image and stamp==.99
    camera.latest_timestamp=.1
    with pytest.raises(TimeoutError):ns[fn.name](camera,250)


def test_calibration_command_stops_pending_dispatch_without_sending_more_actions(tmp_path,monkeypatch):
    from omx_manager.cycle_calibration import action_paused
    path=tmp_path/'cmd.json';monkeypatch.setenv('OMX_CYCLE_CALIBRATION','1');monkeypatch.setenv('OMX_SESSION','s')
    monkeypatch.setenv('OMX_CALIBRATION_COMMAND',str(path))
    assert action_paused()
    C.write_json(path,dict(session='s',command='START'));assert not action_paused()
    C.write_json(path,dict(session='s',command='CONFIRM'));assert action_paused()


def test_calibration_samples_real_joint_feedback_without_camera_and_never_writes_motors(monkeypatch):
    from omx_manager import cycle_calibration as CC
    cfg=trained();clock=Clock();device=NS(get_joint_observation=Mock(return_value=pose(wrist=-10)))
    monkeypatch.setattr(CC,'heartbeat',lambda:None)
    samples=CC.collect_return(device,cfg,clock,clock.sleep,lambda:False)
    assert len(samples)>=3 and all(q['wrist_flex']==-10 for q in samples)
    with pytest.raises(InterruptedError):CC.collect_return(device,cfg,clock,clock.sleep,lambda:True)


def test_calibration_launch_is_item_specific_and_has_no_order_or_workstation_commands():
    from omx_manager.runtime import build
    cfg=trained('omx2')
    argv,_,env,generated=build(cfg,'cfg.yaml',calibration_item='D')
    selected=C.path_value(C.profile(cfg)['items']['D']['path'])
    assert '--policy.path='+str(selected) in argv
    assert '--strategy.auto_restart_on_home=false' in argv
    assert '--strategy.workstation_wait_enabled=false' in argv
    assert env['OMX_CALIBRATION_ITEM']=='D' and env['OMX_CYCLE_CALIBRATION']=='1' and not generated


@pytest.mark.parametrize('device',['omx3','omx4'])
def test_actual_unload_controller_uses_learned_cycles_and_atomic_camera_without_pose_stages(device,basket_cfg,tmp_path,monkeypatch):
    from test_workflow_recovery import controller
    cfg=trained(device,wrist=0);cfg['unload']=basket_cfg['unload']
    cfg['workflow']={'items':{}}
    run,phases,sent,done,path=controller(cfg,tmp_path,monkeypatch,paired=True,jobs=2)
    run()
    assert len(done)==2 and phases.count('RUNNING')==4
    assert all(row['estimated_transfers']==2 and row['basket_empty_verified'] for row in done)
    status=json.loads(path.with_name('basket_status.json').read_text())
    assert status['verdict']=='UNKNOWN' and status['reason']=='POLICY_DONE'


def test_actual_calibration_rollout_records_three_operator_confirmed_cycles_without_mqtt(tmp_path,monkeypatch):
    from omx_manager import cycle_calibration as CC
    cfg=trained();clock=Clock();raw=NS(_omx_manager_snapshot=cfg,q=pose())
    raw.get_joint_observation=lambda:raw.q.copy()
    command=tmp_path/'cmd.json';status=tmp_path/'status.json';output=tmp_path/'captures.json'
    for key,value in dict(OMX_CALIBRATION_COMMAND=command,OMX_CALIBRATION_STATUS=status,OMX_CALIBRATION_OUTPUT=output,
                          OMX_SESSION='s',OMX_CALIBRATION_ITEM='A').items():monkeypatch.setenv(key,str(value))
    monkeypatch.setattr(CC,'time',NS(perf_counter=clock,sleep=clock.sleep,time=clock))
    monkeypatch.setattr(CC,'heartbeat',lambda:None)
    monkeypatch.setattr(CC,'initialize_home',lambda raw,stopped:setattr(raw,'q',pose()))
    class Stop:
        def is_set(self):return clock()>60
        def wait(self,dt):clock.sleep(dt)
    class Timer:
        def __init__(self,*a,**kw):pass
        def tick(self,**kw):pass
        def restart(self):pass
        def wait(self):clock.sleep(.05)
    emitted=0;sample=0
    def commands(_):
        nonlocal emitted
        row=json.loads(status.read_text())
        op=None
        if row['phase']=='READY':op='STOP' if row['cycles']==3 else 'START'
        if row['phase']=='RUNNING' and sample>=120:op='CONFIRM'
        if row['phase']=='CAPTURED':op='HOME'
        if op:emitted+=1
        return dict(session='s',seq=emitted,command=op or 'WAIT')
    monkeypatch.setattr(CC,'read_json',commands)
    def observe(*a):
        nonlocal sample
        sample+=1;raw.q=pose(30) if sample<30 else pose(wrist=-10)
        return raw.q.copy(),False
    def reset():
        nonlocal sample
        sample=0
    strategy=NS(_engine=Mock(),_interpolator=NS(multiplier=1,needs_new_action=lambda:True),reset_control_state=reset,
                _get_observation_with_camera_recovery=observe,_process_observation_and_notify=lambda p,o:o)
    ctx=NS(hardware=NS(robot_wrapper=NS(inner=raw)),runtime=NS(cfg=NS(fps=30),shutdown_event=Stop(),cadence_report=None),processors=None)
    CC.run_rollout(strategy,ctx,lambda *a:{'action':'sent'},Timer)
    captured=json.loads(output.read_text())
    assert len(captured['captures'])==3
    learned=learn_profile(cfg,'A',captured['captures'])
    assert learned['center']['wrist_flex']==-10 and learned['confirmed_cycles']==3
    assert cfg['home']['target']==pose()


def test_calibration_owner_death_never_allows_more_dispatch(tmp_path,monkeypatch):
    from omx_manager import cycle_calibration as CC
    path=tmp_path/'cmd.json';C.write_json(path,dict(session='s',command='START'))
    monkeypatch.setenv('OMX_CYCLE_CALIBRATION','1');monkeypatch.setenv('OMX_SESSION','s')
    monkeypatch.setenv('OMX_CALIBRATION_COMMAND',str(path));monkeypatch.setenv('OMX_CALIBRATION_PARENT_PID','99999999')
    assert not CC.parent_alive() and CC.action_paused()


def test_confirm_arriving_during_inference_prevents_real_dispatch(tmp_path,monkeypatch):
    from omx_manager import cycle_calibration as CC
    path=tmp_path/'cmd.json';C.write_json(path,dict(session='s',command='START'))
    monkeypatch.setenv('OMX_CYCLE_CALIBRATION','1');monkeypatch.setenv('OMX_SESSION','s')
    monkeypatch.setenv('OMX_CALIBRATION_COMMAND',str(path));monkeypatch.delenv('OMX_CALIBRATION_PARENT_PID',raising=False)
    fn=method('lerobot_unified/src/lerobot/rollout/strategies/core.py','send_next_action')
    engine=Mock();interp=Mock();interp.needs_new_action.return_value=True
    def infer(_):
        C.write_json(path,dict(session='s',command='CONFIRM'))
        return Tensor([0]*6)
    engine.get_action.side_effect=infer;wrapper=Mock()
    ctx=NS(policy=NS(inference=engine),data=NS(dataset_features={},ordered_action_keys=list(C.JOINTS)),
           runtime=NS(shutdown_event=NS(is_set=lambda:False)),hardware=NS(robot_wrapper=wrapper),processors=NS(robot_action_processor=lambda x:x[0]))
    ns=dict(os=os,Path=Path,contextlib=contextlib,build_dataset_frame=lambda *a,**kw:{},OBS_STR='observation')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'actual dispatcher','exec'),ns)
    assert ns['send_next_action']({}, {},ctx,interp) is None
    wrapper.send_action.assert_not_called()


def test_watch_opens_one_camera_and_updates_presence_then_empty_without_old_labels(basket_cfg,monkeypatch,capsys):
    from omx_manager import basket
    clock=Clock();camera=NS(is_connected=True);calls=[]
    camera.connect=Mock(side_effect=lambda:clock.sleep(5))
    camera.disconnect=Mock()
    def read(**kwargs):
        calls.append(clock());return frame(len(calls)<=5),clock()
    camera.read_latest_with_timestamp=read
    monkeypatch.setattr(commissioning,'camera_for',lambda _:camera)
    monkeypatch.setattr(commissioning,'require_home',lambda _:None)
    monkeypatch.setattr(commissioning,'time',NS(perf_counter=clock,sleep=clock.sleep,time=clock))
    monkeypatch.setattr(basket,'time',NS(perf_counter=clock))
    device=NS(read=lambda:pose())
    result=commissioning.basket_operation(basket_cfg,device,watch=True,samples_limit=8)
    camera.connect.assert_called_once();camera.disconnect.assert_called_once()
    assert result['verdict']=='EMPTY' and len(calls)==8 and result['connection_s']==5
    rows=[json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]
    verdicts=[row['verdict'] for row in rows]
    assert 'OCCUPIED' in verdicts and verdicts[-2:]==['UNKNOWN','EMPTY']


def test_watch_interrupt_releases_camera_without_motor_writes(basket_cfg,monkeypatch):
    camera=NS(is_connected=True,connect=Mock(),disconnect=Mock(),read_latest_with_timestamp=Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(commissioning,'camera_for',lambda _:camera)
    monkeypatch.setattr(commissioning,'require_home',lambda _:None)
    device=NS(read=lambda:pose(),send=Mock())
    with pytest.raises(KeyboardInterrupt):commissioning.basket_operation(basket_cfg,device,watch=True)
    camera.disconnect.assert_called_once();device.send.assert_not_called()


def test_normal_manager_never_inherits_calibration_control_flags(monkeypatch):
    from omx_manager.runtime import build
    monkeypatch.setenv('OMX_CYCLE_CALIBRATION','1');monkeypatch.setenv('OMX_CALIBRATION_COMMAND','stale.json')
    _,_,env,_=build(trained(),'cfg.yaml')
    assert 'OMX_CYCLE_CALIBRATION' not in env and 'OMX_CALIBRATION_COMMAND' not in env


@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
def test_return_sampling_waits_out_initial_motion_without_relaxing_span(device,monkeypatch):
    from omx_manager import cycle_calibration as CC
    cfg=trained(device);clock=Clock();calls=[]
    def observe():
        calls.append(clock())
        return pose(wrist=-10+max(0.,2.-clock()*4),grip=30+clock()*10)
    monkeypatch.setattr(CC,'heartbeat',lambda:None)
    samples=CC.collect_return(NS(get_joint_observation=observe),cfg,clock,clock.sleep,lambda:False)
    assert clock()>.6 and clock()<CC.options(cfg)['calibration_settle_timeout_s']
    assert len(samples)>=3 and calls[-1]-calls[-len(samples)]>=.6-1e-9
    assert max(q['wrist_flex'] for q in samples)-min(q['wrist_flex'] for q in samples)<=cfg['home']['capture_max_span']


def test_unstable_return_is_bounded_reports_joint_and_never_saves_or_commands(monkeypatch):
    from omx_manager import cycle_calibration as CC
    cfg=trained();cfg['counting']['calibration_settle_timeout_s']=1.
    clock=Clock();device=NS(send=Mock());count=0
    def observe():
        nonlocal count
        count+=1;return pose(wrist=-10 if count%2 else -8)
    device.get_joint_observation=observe
    monkeypatch.setattr(CC,'heartbeat',lambda:None)
    with pytest.raises(CC.ReturnNotSettled,match=r'wrist_flex span=2.000 limit=0.800'):
        CC.collect_return(device,cfg,clock,clock.sleep,lambda:False)
    assert clock()<=1.06;device.send.assert_not_called()


def test_return_feedback_gap_requires_new_full_hold(monkeypatch):
    from omx_manager import cycle_calibration as CC
    cfg=trained();clock=Clock();count=0
    def observe():
        nonlocal count
        count+=1
        if count==8:clock.sleep(.3)
        return pose(wrist=-10)
    monkeypatch.setattr(CC,'heartbeat',lambda:None)
    samples=CC.collect_return(NS(get_joint_observation=observe),cfg,clock,clock.sleep,lambda:False)
    assert clock()>=1.25-1e-9 and len(samples)>=3


def test_settling_never_learns_stretched_pose_or_ignores_owner_death(monkeypatch):
    from omx_manager import cycle_calibration as CC
    cfg=trained();clock=Clock();device=NS(get_joint_observation=Mock(return_value=pose(40)))
    with pytest.raises(C.ConfigError,match='stretched'):
        CC.collect_return(device,cfg,clock,clock.sleep,lambda:False)
    monkeypatch.setattr(CC,'parent_alive',lambda:False)
    device.get_joint_observation.reset_mock()
    with pytest.raises(InterruptedError):CC.collect_return(device,cfg,clock,clock.sleep,lambda:False)
    device.get_joint_observation.assert_not_called()


def test_calibration_rejected_window_keeps_session_paused_and_retries_observation_only(tmp_path,monkeypatch):
    from omx_manager import cycle_calibration as CC
    cfg=trained();clock=Clock();raw=NS(_omx_manager_snapshot=cfg,q=pose(wrist=-10))
    command=tmp_path/'cmd.json';status=tmp_path/'status.json';output=tmp_path/'captures.json'
    for key,value in dict(OMX_CALIBRATION_COMMAND=command,OMX_CALIBRATION_STATUS=status,OMX_CALIBRATION_OUTPUT=output,
                          OMX_SESSION='s',OMX_CALIBRATION_ITEM='A').items():monkeypatch.setenv(key,str(value))
    monkeypatch.setattr(CC,'time',NS(perf_counter=clock,sleep=clock.sleep,time=clock))
    monkeypatch.setattr(CC,'heartbeat',lambda:None)
    captures=0;seq=0;sends=0;reject_seen=False;phases=[]
    engine=Mock()
    def commands(_):
        nonlocal seq,reject_seen
        row=json.loads(status.read_text());phases.append(row['phase'])
        if row['phase']=='READY':op='START'
        elif row['phase']=='RUNNING':op='CONFIRM' if sends>=35 else 'WAIT'
        elif row['phase']=='CAPTURE_RETRY':
            reject_seen=True
            assert not output.exists() and engine.resume.call_count==1
            op='CONFIRM'
        elif row['phase']=='CAPTURED':op='STOP'
        else:op='WAIT'
        if op!='WAIT':seq+=1
        return dict(session='s',seq=seq,command=op)
    def collect(*args):
        nonlocal captures
        captures+=1
        if captures==1:raise CC.ReturnNotSettled('wrist_roll span=1.0 limit=0.8')
        return [pose(wrist=-10)]*4
    def send(*args):
        nonlocal sends
        sends+=1;return {'sent':True}
    monkeypatch.setattr(CC,'read_json',commands);monkeypatch.setattr(CC,'collect_return',collect)
    class Stop:
        def is_set(self):return clock()>10
        def wait(self,dt):clock.sleep(dt)
    class Timer:
        def __init__(self,*a,**kw):pass
        def tick(self,**kw):pass
        def restart(self):pass
        def wait(self):clock.sleep(.05)
    strategy=NS(_engine=engine,_interpolator=NS(multiplier=1,needs_new_action=lambda:True),reset_control_state=lambda:None,
                _get_observation_with_camera_recovery=lambda *a:(raw.q.copy(),False),_process_observation_and_notify=lambda p,o:o)
    ctx=NS(hardware=NS(robot_wrapper=NS(inner=raw)),runtime=NS(cfg=NS(fps=30),shutdown_event=Stop(),cadence_report=None),processors=None)
    CC.run_rollout(strategy,ctx,send,Timer)
    rows=json.loads(output.read_text())['captures']
    assert reject_seen and captures==2 and sends==35 and engine.resume.call_count==1
    assert len(rows)==1 and rows[0]['actions']==35 and rows[0]['duration_s']<2


def test_settle_timeout_is_configurable_but_must_exceed_hold():
    cfg=trained();cfg['counting']['calibration_settle_timeout_s']=.6
    with pytest.raises(C.ConfigError):C.validate(cfg)
