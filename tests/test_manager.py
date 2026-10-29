"""Hardware-free tests of the actual manager, detector, journal and process cleanup."""
import ast
import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import time
from unittest.mock import Mock
import pytest
from omx_manager import config as C
from omx_manager import hardware as H
from omx_manager import runtime as R
from omx_manager.home import HomeDetector, errors, move_home, stable_pose
from omx_manager.cli import edit_value, invoke
from omx_manager.simulation import Clock, FakeFollower, exercise
from omx_manager.journal import JobJournal

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def cfg():
    c=C.load(ROOT/'params/omx1.yaml')
    c['home']['target']={j:0. for j in C.JOINTS}
    c['counting']['min_cycle_s']=0
    return c

@pytest.fixture
def commissioned(cfg,tmp_path):
    cfg=copy.deepcopy(cfg)
    cal=tmp_path/'omx_follower_arm.json'
    C.write_json(cal,{j:{'id':C.IDS[j],'drive_mode':0,'range_min':0,'range_max':4095,'homing_offset':0} for j in C.JOINTS})
    cfg['hardware']['calibration_file']=str(cal)
    for item,entry in C.profile(cfg)['items'].items():
        folder=tmp_path/item;folder.mkdir()
        model={'type':'act','output_features':{'action':{'shape':[6]}},'input_features':{'observation.images.'+key:{'shape':[3,cam['height'],cam['width']]} for key,cam in cfg['hardware']['cameras'].items()}}
        C.write_json(folder/'config.json',model)
        (folder/'model.safetensors').write_bytes(b'FAKE: never loaded')
        for p in ('policy_preprocessor.json','policy_postprocessor.json'): C.write_json(folder/p,{'steps':[]})
        entry['path']=str(folder)
    cfg['home']['binding']=C.binding(cfg)
    return cfg

def q(value=0.): return {j:value for j in C.JOINTS}

@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
def test_all_device_templates_and_simulations(device):
    c=C.load(ROOT/f'params/{device}.yaml')
    assert c['home']['target'] is None
    assert exercise(c)['counted_cycles']==8
    assert c['home']['target'] is None  # simulation must not install a fake home
    argv,mqtt,env,_=R.build(c,ROOT/f'params/{device}.yaml')
    assert '--return_to_initial_position=false' in argv
    assert '--robot.id='+c['hardware']['robot_id'] in argv
    assert env['OMX_ACT_PREFETCH']=='0'
    assert '--strategy.workstation_wait_enabled=true' in argv if c['role']=='unload' else '--strategy.auto_restart_on_home=true' in argv

@pytest.mark.parametrize('value',[float('nan'),float('inf'),True,-1,0])
def test_invalid_tolerance_rejected(cfg,value):
    cfg['home']['tolerance']['wrist_flex']=value
    with pytest.raises(C.ConfigError): C.validate(cfg)

@pytest.mark.parametrize('field,value',[('cpu_threads',8),('cpu_threads',True),('broker_port',0),('fps',0)])
def test_invalid_runtime_rejected(cfg,field,value):
    cfg['runtime'][field]=value
    with pytest.raises(C.ConfigError): C.validate(cfg)

def test_unsupported_quantized_runtime_fails(cfg):
    cfg['models']['active_profile']='int8_candidate'
    with pytest.raises(C.ConfigError,match='Only lerobot'): C.validate(cfg)

def test_model_and_calibration_binding(commissioned):
    C.check_files(commissioned); C.require_home(commissioned)
    commissioned['runtime']['cpu_threads']=3
    C.require_home(commissioned)  # CPU tuning does not invalidate physical Home
    C.profile(commissioned)['items']['A']['task']='different task'
    with pytest.raises(C.ConfigError,match='binding'): C.require_home(commissioned)

def test_model_camera_mismatch_is_caught(commissioned):
    folder=C.path_value(C.profile(commissioned)['items']['A']['path'])
    m=json.loads((folder/'config.json').read_text());m['input_features']={'observation.images.top':{'shape':[3,480,640]}}
    C.write_json(folder/'config.json',m)
    with pytest.raises(C.ConfigError,match='camera keys'): C.check_files(commissioned)

def test_wrong_calibration_id_is_caught(commissioned):
    p=C.path_value(commissioned['hardware']['calibration_file'])
    data=json.loads(p.read_text());data['wrist_roll']['id']=999;C.write_json(p,data)
    with pytest.raises(C.ConfigError,match='identity'): C.check_files(commissioned)

def test_calibration_name_must_match_runtime(commissioned):
    commissioned['hardware']['robot_id']='other'
    with pytest.raises(C.ConfigError,match='filename'): C.check_files(commissioned,models=False)

def test_atomic_save_backup_and_edit_validation(cfg,tmp_path):
    p=tmp_path/'settings.yaml';C.save(p,cfg);old=p.read_bytes()
    updated=edit_value(cfg,'home.tolerance.wrist_flex','5.5');C.save(p,updated)
    assert C.load(p)['home']['tolerance']['wrist_flex']==5.5
    assert next((tmp_path/'backups').iterdir()).read_bytes()==old
    with pytest.raises(C.ConfigError):edit_value(cfg,'runtime.cpu_threads','900')
    with pytest.raises(ValueError):edit_value(cfg,'hardware.port','other robot')

def test_stationary_pose_never_counts(cfg):
    d=HomeDetector(cfg)
    assert not any(d.update(q(),i*.05) for i in range(500))

def test_one_count_then_requires_new_departure(cfg):
    d=HomeDetector(cfg);d.update(q(20),0.)
    counts=sum(d.update(q(),i*.05) for i in range(1,150))
    assert counts==1

def test_departure_before_minimum_time_is_remembered(cfg):
    cfg['counting']['min_cycle_s']=5
    d=HomeDetector(cfg);d.update(q(20),.1)
    counts=sum(d.update(q(),i*.05) for i in range(3,130))
    assert counts==1

def test_gap_cannot_satisfy_home_hold(cfg):
    d=HomeDetector(cfg);d.update(q(20),0)
    d.update(q(),.05);d.update(q(),.1)
    assert not d.update(q(),2.1)
    assert not d.update(q(),2.15)

def test_nan_and_missing_joint_invalidate_hold(cfg):
    d=HomeDetector(cfg);d.update(q(20),0);d.update(q(),.05)
    bad=q();bad['elbow_flex']=float('nan')
    with pytest.raises(C.ConfigError): d.update(bad,.1)
    assert d.since is None
    with pytest.raises(C.ConfigError): d.update({},.15)

def test_per_joint_tolerances_not_only_maximum(cfg):
    cfg['home']['tolerance']['gripper']=1
    d=HomeDetector(cfg);d.update(q(20),0)
    sample=q();sample['gripper']=2
    assert not any(d.update(sample,i*.05) for i in range(1,100))
    assert not d.rows['gripper']['ok']

def test_slow_progress_near_home_never_raises_or_stalls(cfg):
    cfg['counting']['stall_s']=1
    d=HomeDetector(cfg);d.update(q(20),0)
    for i in range(1,120):
        sample=q(13-i*.04);d.update(sample,i*.05)
    assert not d.stalled

def test_real_near_home_stall_is_diagnostic(cfg):
    cfg['counting']['stall_s']=1
    d=HomeDetector(cfg);d.update(q(20),0)
    for i in range(1,70):d.update(q(10),i*.05)
    assert d.stalled  # no exception and no false count

def test_capture_stable_average_and_unstable_rejection():
    assert stable_pose([q(.1),q(.2),q(.3)],.3)['elbow_flex']==pytest.approx(.2)
    with pytest.raises(C.ConfigError,match='moving'):stable_pose([q(),q(4),q()],.8)

def test_home_movement_is_bounded_and_reaches_strict_arrival(cfg):
    clock=Clock();device=FakeFollower(q(10))
    result=move_home(device,cfg,clock=clock,sleep=clock.sleep)
    assert max(result.values())<=cfg['home']['arrival_tolerance']
    previous=q(10)
    for sent in device.sent:
        assert max(abs(sent[j]-previous[j]) for j in C.JOINTS)<=cfg['home']['max_step']+1e-9
        previous=sent

def test_far_home_is_refused_before_any_action(cfg):
    device=FakeFollower(q(40));clock=Clock()
    with pytest.raises(C.ConfigError,match='approach'):move_home(device,cfg,clock=clock,sleep=clock.sleep)
    assert not device.sent

def test_home_stop_has_no_actions(cfg):
    device=FakeFollower(q(10));clock=Clock()
    with pytest.raises(InterruptedError):move_home(device,cfg,clock=clock,sleep=clock.sleep,stopped=lambda:True)
    assert not device.sent

def test_home_timeout_does_not_count_success(cfg):
    device=FakeFollower(q(10));device.send=lambda _:None;clock=Clock();cfg['home']['move_timeout_s']=1
    with pytest.raises(TimeoutError):move_home(device,cfg,clock=clock,sleep=clock.sleep)

def test_home_feedback_stall_refuses_next_action(cfg):
    device=FakeFollower(q(10));clock=Clock()
    with pytest.raises(TimeoutError,match='feedback'):move_home(device,cfg,clock=clock,sleep=lambda _:clock.sleep(1))
    assert len(device.sent)==1

def test_device_lock_excludes_concurrent_manager(tmp_path):
    p=tmp_path/'device.lock'
    with H.Lock(p):
        with pytest.raises(RuntimeError):
            with H.Lock(p):pass
    with H.Lock(p):pass

def test_cleanup_continues_after_failed_engine_camera_and_bus():
    engine=Mock();engine.stop.side_effect=RuntimeError('engine failure')
    cam=Mock();cam.disconnect.side_effect=RuntimeError('camera failure')
    bus=Mock(is_connected=True);bus.disconnect.side_effect=RuntimeError('bus failure')
    robot=SimpleNamespace(cameras={'wrist':cam},bus=bus,config=SimpleNamespace(disable_torque_on_disconnect=False))
    result=H.cleanup_resources(engine,robot)
    assert len(result)==3
    bus.port_handler.closePort.assert_called_once()

def test_cleanup_closes_bus_even_if_camera_is_disconnected():
    bus=Mock(is_connected=True);cam=Mock(is_connected=False)
    robot=SimpleNamespace(is_connected=False,cameras={'wrist':cam},bus=bus,config=SimpleNamespace(disable_torque_on_disconnect=False))
    H.cleanup_resources(robot=robot)
    bus.disconnect.assert_called_once_with(disable_torque=False)
    cam.disconnect.assert_called_once()

def test_durable_job_dedup_and_interrupted_replay(tmp_path):
    p=tmp_path/'jobs.json';j=JobJournal(p);j.begin('one',{'A':2})
    assert JobJournal(p).lookup('one',{'A':2})['state']=='RUNNING'
    with pytest.raises(ValueError):j.begin('one',{'A':2})
    with pytest.raises(ValueError):j.lookup('one',{'A':3})
    j.finish('one',True,'done')
    assert JobJournal(p).lookup('one',{'A':2})['success'] is True

def test_startup_preserves_unfinished_inventory(tmp_path):
    p=tmp_path/'inventory.json';R.initialize_inventory(p)
    data=json.loads(p.read_text());data.update(remaining=2,order_completed=3);C.write_json(p,data)
    before=p.read_bytes()
    with pytest.raises(R.OperatorRecoveryRequired):R.initialize_inventory(p)
    assert p.read_bytes()==before

def test_operator_reconcile_archives_counts_and_keeps_job_history(cfg,tmp_path,monkeypatch):
    monkeypatch.setattr(R,'state_dir',lambda _:tmp_path)
    C.write_json(tmp_path/'inventory.json',{'remaining':2,'order_completed':3})
    journal=JobJournal(tmp_path/'jobs.json');journal.begin('interrupted',{'A':5})
    R.acknowledge_recovery(cfg)
    assert journal.lookup('interrupted',{'A':5})['state']=='INTERRUPTED'
    assert json.loads(next((tmp_path/'archive').iterdir()).read_text())['order_completed']==3

def test_stale_or_wrong_process_heartbeat_refused(tmp_path):
    p=tmp_path/'heartbeat.json';C.write_json(p,{'pid':1,'monotonic':10})
    assert R.healthy_heartbeat(p,1,11,2)
    assert not R.healthy_heartbeat(p,2,11,2)
    assert not R.healthy_heartbeat(p,1,20,2)
    assert not R.healthy_heartbeat(p,1,9,2)

def test_real_subprocess_kill_timeout():
    proc=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("READY",flush=True);time.sleep(60)'],start_new_session=True,stdout=subprocess.PIPE,text=True)
    try:
        assert proc.stdout.readline().strip()=='READY'
        R.stop_process(proc,.1)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None: os.killpg(proc.pid,signal.SIGKILL);proc.wait()
        proc.stdout.close()

def test_supervisor_starts_only_after_ready_and_cleans_both_children(commissioned,tmp_path,monkeypatch):
    cfg=commissioned;cfg['runtime']['shutdown_timeout_s']=.1
    monkeypatch.setattr(R,'state_dir',lambda _:tmp_path)
    h=tmp_path/'heartbeat.json'
    worker_code='import os,json,time,pathlib; p=pathlib.Path('+repr(str(h))+');'+ '\nwhile True:\n p.write_text(json.dumps({"pid":os.getpid(),"monotonic":time.monotonic(),"ready":True})); time.sleep(.05)'
    real_build=R.build
    def fake_build(c,p):
        _,_,env,_=real_build(c,p)
        return [sys.executable,'-c',worker_code],[sys.executable,'-c','raise SystemExit(7)'],env,{}
    monkeypatch.setattr(R,'build',fake_build)
    started=[];popen=R.subprocess.Popen
    def record(*a,**kw):
        p=popen(*a,**kw);started.append(p);return p
    monkeypatch.setattr(R.subprocess,'Popen',record)
    supervisor=R.Supervisor(tmp_path/'config.yaml')
    with pytest.raises(RuntimeError,match='MQTT exit'):supervisor.run_once(cfg)
    assert len(started)==2 and all(p.poll() is not None for p in started)

def test_startup_error_before_popen_on_unregistered_home(cfg,tmp_path,monkeypatch):
    cfg['home']['target']=None
    monkeypatch.setattr(R,'check_files',lambda _:None)
    spawn=Mock();monkeypatch.setattr(R.subprocess,'Popen',spawn)
    with pytest.raises(C.ConfigError,match='registered'):R.Supervisor(tmp_path/'none.yaml').run_once(cfg)
    spawn.assert_not_called()

def test_physical_commands_require_explicit_motion_flag(tmp_path,cfg):
    p=tmp_path/'c.yaml';C.save(p,cfg)
    with pytest.raises(ValueError,match='confirm-motion'):invoke(p,'run')
    with pytest.raises(ValueError,match='confirm-motion'):invoke(p,'home')

# Compile actual methods without importing PyTorch/SDK to exercise real driver guards.
def driver_class():
    tree=ast.parse((ROOT/'lerobot_unified/src/lerobot/robots/omx_follower/omx_follower.py').read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='OmxFollower')
    names={'connect','calibrate','disconnect','_check_motor_health'}
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names]
    for n in methods:n.decorator_list=[]
    new=ast.ClassDef(name='Driver',bases=[],keywords=[],body=methods,decorator_list=[])
    ns={'time':time,'logger':Mock()};exec(compile(ast.fix_missing_locations(ast.Module(body=[new],type_ignores=[])),'actual_driver','exec'),ns)
    return ns['Driver']

def test_actual_follower_calibration_is_never_written():
    driver=driver_class()();driver.bus=Mock()
    with pytest.raises(RuntimeError,match='disabled'):driver.calibrate()
    assert not driver.bus.mock_calls

def test_actual_follower_connection_failure_releases_port_and_cameras(tmp_path,monkeypatch):
    driver=driver_class()();driver.config=SimpleNamespace(port='fake',disable_torque_on_disconnect=False)
    driver.bus=Mock(is_connected=True);driver.calibration={'valid':True};driver.is_calibrated=False
    driver.cameras={'front':Mock()}
    monkeypatch.setattr(H,'port_lock',lambda _:H.Lock(tmp_path/'motor.lock'))
    with pytest.raises(RuntimeError,match='calibration'):driver.connect()
    driver.bus.write_calibration.assert_not_called()
    driver.bus.port_handler.closePort.assert_called_once()
    with H.Lock(tmp_path/'motor.lock'):pass

@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
def test_fifty_runs_eight_cycles_each(device):
    cfg=C.load(ROOT/f'params/{device}.yaml')
    for _ in range(50): assert exercise(cfg)['counted_cycles']==8

def actual_base_method(name):
    tree=ast.parse((ROOT/'lerobot_unified/src/lerobot/rollout/strategies/base.py').read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='BaseStrategy')
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
    fn.decorator_list=[]
    fn.returns=None
    for arg in fn.args.args:arg.annotation=None
    return fn

class FakeTimer:
    def __init__(self,clock):self.clock=clock
    def tick(self,**_):pass
    def section(self,_):
        from contextlib import nullcontext
        return nullcontext()
    def wait(self):self.clock.sleep(.05)
    def restart(self):pass
    def log_run_summary(self):pass
    def log_episode_summary(self,*a,**k):pass


def production_strategy_fixture(cfg,method,monkeypatch):
    import threading
    from types import SimpleNamespace as NS
    clock=Clock();event=threading.Event();engine=Mock();interpolator=Mock()
    interpolator.multiplier=1
    detector=HomeDetector(cfg)
    manager=NS(update=detector.update,reset=detector.reset,detector=detector)
    robot=NS()
    ctx=NS(runtime=NS(cfg=NS(fps=30,duration=0,use_torch_compile=False),shutdown_event=event,cadence_report=None),hardware=NS(robot_wrapper=robot,initial_position={j+'.pos':0 for j in C.JOINTS}),processors=NS())
    fn=actual_base_method(method)
    observations=iter([{j+'.pos':v for j,v in sample.items()} for sample in (([q()] if method=='_run_persistent_workstation' else [])+[q(20)]+[q()]*100)])
    ns={'time':NS(perf_counter=clock,monotonic=clock,sleep=clock.sleep),'os':os,'Path':Path,'logger':Mock(),'CycleTimer':lambda *a,**k:FakeTimer(clock),'send_next_action':Mock()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'production_base','exec'),ns)
    strategy=NS(_engine=engine,_interpolator=interpolator,_manager_home=manager,_cached_obs_processed=None,
        config=NS(workstation_wait_enabled=method=='_run_persistent_workstation',auto_restart_on_home=True,keep_alive_when_inventory_empty=False,basket_success_enabled=False,stuck_basket_mask_timeout_s=5,stuck_basket_mask_enabled=False,pickup_cycle_timeout_s=180,home_tolerance=7,home_leave_tolerance=12,home_hold_s=.5,min_cycle_s=0,inventory_capacity=8,idle_complete_enabled=False,stuck_basket_mask_max_retries=0),
        _remaining_inventory=1,_total_completed=7,_get_observation_with_camera_recovery=lambda *a:(next(observations),False),_handle_warmup=lambda *a:False,
        _process_observation_and_notify=lambda *a:{},_log_telemetry=Mock(),_max_home_error=lambda obs,home:max(obs.values()),reset_control_state=Mock())
    monkeypatch.setattr('omx_manager.hooks.heartbeat',lambda *a:None)
    monkeypatch.setattr('omx_manager.hooks.initialize_home',lambda *a,**kw:None)
    return ns,strategy,ctx,event,clock


def test_actual_pickup_control_loop_uses_shared_counter(cfg,monkeypatch):
    ns,s,ctx,event,clock=production_strategy_fixture(cfg,'run',monkeypatch)
    counts=[]
    def count():counts.append(clock());s._remaining_inventory=0
    s._count_completed_cycle=count
    ns['run'](s,ctx)
    assert len(counts)==1
    assert clock()<5  # immediate departure, not the legacy 5-second arming trap


def test_actual_persistent_unload_loop_requires_departure_and_continuous_hold(cfg,tmp_path,monkeypatch):
    ns,s,ctx,event,clock=production_strategy_fixture(cfg,'_run_persistent_workstation',monkeypatch)
    s.config.workstation_status_file=str(tmp_path/'status.json');s.config.workstation_control_file=str(tmp_path/'command.json')
    s.config.workstation_poll_s=.1;s.config.workstation_job_timeout_s=180;s.config.idle_complete_joint_delta=.6;s.config.idle_complete_hold_s=.5
    monkeypatch.setenv('OMX_WORKSTATION_SESSION','test-session')
    ns['read_json']=lambda p:{'session':'test-session','job_id':'job','command':'START'}
    ns['new_status']=lambda session,phase,job_id,**kw:dict(session=session,phase=phase,job_id=job_id,**kw)
    statuses=[]
    def status(path,data):
        statuses.append(data)
        if data['phase']=='DONE':event.set()
    ns['atomic_json']=status
    ns['_run_persistent_workstation'](s,ctx)
    assert [d['phase'] for d in statuses]==['READY','RUNNING','DONE']
    assert statuses[-1]['physical_unloading_verified'] is False


def test_mqtt_completed_command_replays_result_without_actuation(tmp_path,monkeypatch):
    import types
    mqtt=types.ModuleType('paho.mqtt.client');mqtt.CallbackAPIVersion=SimpleNamespace(VERSION1=1);mqtt.Client=lambda *_:Mock()
    paho=types.ModuleType('paho');paho.__path__=[]
    parent=types.ModuleType('paho.mqtt');parent.__path__=[];parent.client=mqtt;paho.mqtt=parent
    monkeypatch.setitem(sys.modules,'paho',paho);monkeypatch.setitem(sys.modules,'paho.mqtt',parent);monkeypatch.setitem(sys.modules,'paho.mqtt.client',mqtt)
    ipc_spec=importlib.util.spec_from_file_location('lerobot.rollout.workstation_ipc',ROOT/'lerobot_unified/src/lerobot/rollout/workstation_ipc.py')
    ipc=importlib.util.module_from_spec(ipc_spec);ipc_spec.loader.exec_module(ipc)
    monkeypatch.setitem(sys.modules,'lerobot.rollout.workstation_ipc',ipc)
    monkeypatch.setattr(sys,'argv',['client','--robot-id','omx1','--broker-ip','localhost'])
    monkeypatch.setenv('OMX_JOB_JOURNAL',str(tmp_path/'jobs.json'))
    spec=importlib.util.spec_from_file_location('tested_mqtt_client',ROOT/'lerobot_unified/omx_mqtt_client.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.journal.begin('done',{'B':1});m.journal.finish('done',True,'already complete')
    ack=Mock();result=Mock();m.send_ack=ack;m.send_result=result;m.run_job=Mock()
    m.on_command({'job_id':'done','items':{'B':1}})
    ack.assert_called_once_with('done',True);result.assert_called_once_with('done',True,'already complete');m.run_job.assert_not_called()
    ack.reset_mock();m.on_command({'job_id':'done','items':{'B':2}});ack.assert_called_once_with('done',False)
    ack.reset_mock();m.on_message(None,None,SimpleNamespace(payload=b'{"job_id":"retained","items":{"A":1}}',topic='omx1/command',retain=True))
    ack.assert_not_called()


def test_supervisor_backoff_and_explicit_stop_prevent_restart(cfg,tmp_path,monkeypatch):
    p=tmp_path/'config.yaml';C.save(p,cfg);supervisor=R.Supervisor(p)
    monkeypatch.setattr(R,'state_dir',lambda _:tmp_path/'runtime')
    attempts=[];waits=[]
    class Event:
        stop=False
        def is_set(self):return self.stop
        def set(self):self.stop=True
        def wait(self,delay):waits.append(delay);return self.stop
    supervisor.stopped=Event()
    def fail(c):
        attempts.append(1)
        if len(attempts)==3:supervisor.stop()
        raise RuntimeError('injected transient fault')
    supervisor.run_once=fail;supervisor.run()
    assert len(attempts)==3
    assert waits[:2]==[cfg['recovery']['retry_initial_s'],cfg['recovery']['retry_initial_s']*2]


def test_same_path_replaced_weights_invalidates_registered_home(commissioned):
    path=C.path_value(C.profile(commissioned)['items']['A']['path'])/'model.safetensors'
    path.write_bytes(b'changed model')
    with pytest.raises(C.ConfigError,match='binding'):C.require_home(commissioned)

def test_blocked_feedback_during_read_never_sends_action(cfg):
    clock=Clock();device=FakeFollower(q(10));read=device.read;calls=[]
    def blocked_read():
        calls.append(1)
        if len(calls)>1:clock.sleep(2)
        return read()
    device.read=blocked_read
    with pytest.raises(TimeoutError,match='feedback'):move_home(device,cfg,clock=clock,sleep=clock.sleep)
    assert not device.sent

def test_actual_unload_hold_avoids_new_chunk_inference(cfg,tmp_path,monkeypatch):
    ns,s,ctx,event,clock=production_strategy_fixture(cfg,'_run_persistent_workstation',monkeypatch)
    s.config.workstation_status_file=str(tmp_path/'status.json');s.config.workstation_control_file=str(tmp_path/'command.json')
    s.config.workstation_poll_s=.1;s.config.workstation_job_timeout_s=180;s.config.idle_complete_joint_delta=.6;s.config.idle_complete_hold_s=2
    detector=HomeDetector(cfg,hold=2)
    s._manager_home=SimpleNamespace(update=detector.update,reset=detector.reset,detector=detector)
    monkeypatch.setenv('OMX_WORKSTATION_SESSION','test-session')
    ns['read_json']=lambda p:{'session':'test-session','job_id':'job','command':'START'}
    ns['new_status']=lambda session,phase,job_id,**kw:dict(session=session,phase=phase,job_id=job_id,**kw)
    ns['atomic_json']=lambda path,data:event.set() if data['phase']=='DONE' else None
    ns['_run_persistent_workstation'](s,ctx)
    assert event.is_set() and clock()>=2
    assert ns['send_next_action'].call_count<10


def test_yaml_duplicate_and_unknown_keys_are_rejected(cfg,tmp_path):
    p=tmp_path/'bad.yaml';C.save(p,cfg)
    p.write_text(p.read_text()+'device_id: omx2\n')
    with pytest.raises(C.ConfigError,match='Duplicate'):C.load(p)
    cfg['home']['tolerence']=3
    with pytest.raises(C.ConfigError,match='Unknown'):C.validate(cfg)

def test_model_mapping_does_not_depend_on_yaml_key_order(cfg):
    items=C.profile(cfg)['items']
    C.profile(cfg)['items']={'B':items['B'],'A':items['A']}
    _,_,_,generated=R.build(cfg,ROOT/'params/omx1.yaml')
    assert generated['order_models.json']['A']['path']==str(C.path_value(items['A']['path']))


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),101.0,-101.0])
def test_actual_policy_send_rejects_invalid_target_before_motor_write(bad):
    tree=ast.parse((ROOT/'lerobot_unified/src/lerobot/robots/omx_follower/omx_follower.py').read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='OmxFollower')
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='send_action')
    fn.decorator_list=[];fn.returns=None
    for arg in fn.args.args:arg.annotation=None
    ns={};exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'actual_send','exec'),ns)
    robot=SimpleNamespace(bus=Mock(),config=SimpleNamespace(max_relative_target=None))
    action={j+'.pos':0. for j in C.JOINTS};action['wrist_flex.pos']=bad
    with pytest.raises(C.ConfigError):ns['send_action'](robot,action)
    robot.bus.sync_write.assert_not_called()


def test_cli_capture_saves_yaml_without_motor_writes(commissioned,tmp_path,monkeypatch):
    from omx_manager import cli
    path=tmp_path/'settings.yaml';commissioned['home']['target']=None;commissioned['home']['binding']=None;C.save(path,commissioned)
    calibration=C.path_value(commissioned['hardware']['calibration_file']);before=calibration.read_bytes()
    device=FakeFollower(q(5));device.__class__ # Pure fake: no leader or SDK
    class Bus:
        def __init__(self,_):pass
        def __enter__(self):return device
        def __exit__(self,*_):device.closed=True
    monkeypatch.setattr(cli,'FollowerBus',Bus)
    monkeypatch.setattr(cli,'device_lock',lambda _:H.Lock(tmp_path/'device.lock'))
    clock=Clock();monkeypatch.setattr(cli.time,'monotonic',clock);monkeypatch.setattr(cli.time,'sleep',clock.sleep)
    cli.invoke(path,'capture')
    registered=C.load(path);C.require_home(registered)
    assert registered['home']['target']==q(5)
    assert calibration.read_bytes()==before and device.sent==[] and device.closed

def test_actual_count_commit_preserves_order_token_and_quantity(tmp_path):
    fn=actual_base_method('_save_inventory')
    ns={'json':json,'time':time,'os':os}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'production_inventory','exec'),ns)
    path=tmp_path/'inventory.json'
    C.write_json(path,dict(order_item='A',order_quantity=2,order_completed=0,order_started_at='registered',order_started_at_epoch=42))
    strategy=SimpleNamespace(_inventory_path=path,config=SimpleNamespace(inventory_capacity=8),_total_completed=7,_remaining_inventory=1)
    ns['_save_inventory'](strategy)
    data=json.loads(path.read_text())
    assert data['order_completed']==1 and data['remaining']==1 and data['order_started_at_epoch']==42
