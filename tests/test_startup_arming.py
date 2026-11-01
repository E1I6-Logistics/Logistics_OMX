"""Actual preflight and manual arm operation with fake motors; no SDK/serial."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from omx_manager import config as C,hardware as H,runtime as R,exposure as E,hooks
from omx_manager import startup_arming as A
from omx_manager.cli import edit_value
from omx_manager.simulation import Clock
from test_manager import cfg,commissioned


class Device:
    def __init__(self,cfg):
        self.cfg=cfg;self.q=dict(cfg['home']['target']);self.goals={j:90. for j in C.JOINTS}
        self.torques={j:0 for j in C.JOINTS};self.modes={j:5 if j=='gripper' else 4 for j in C.JOINTS}
        self.errors={j:0 for j in C.JOINTS};self.events=[];self.motion_sent=False
        self.read_error=None;self.on_read=None;self.on_enable=None
        self.bus=NS(read=self.register,sync_read=self.sync_read,sync_write=self.sync_write,enable_torque=self.enable)
    def register(self,key,joint):
        return {'Torque_Enable':self.torques,'Operating_Mode':self.modes,'Hardware_Error_Status':self.errors}[key][joint]
    def health(self):
        if any(self.errors.values()):raise RuntimeError('Motor hardware error; no motion')
    def read(self):
        self.health()
        if self.read_error:raise self.read_error
        if self.on_read:self.on_read(self)
        return self.q.copy()
    def sync_read(self,key):return self.goals.copy()
    def sync_write(self,key,values):
        assert key=='Goal_Position';self.events.append(('goal',dict(values)));self.goals.update(values)
        if all(self.torques.values()):self.q.update(values)
    def enable(self):
        self.events.append(('enable',self.q.copy()));self.torques={j:1 for j in C.JOINTS}
        if self.on_enable:self.on_enable(self)
    arm=H.FollowerBus.arm
    send=H.FollowerBus.send


@pytest.fixture
def rig(commissioned,tmp_path,monkeypatch):
    cfg=commissioned;cfg['recovery']['startup_arm_at_home']=True
    cfg['recovery']['startup_arm_tolerance']=2.
    device=Device(cfg);clock=Clock();supervisor=R.Supervisor(tmp_path/'cfg.yaml')
    class Bus:
        def __init__(self,*_):pass
        def __enter__(self):return device
        def __exit__(self,*_):pass
    monkeypatch.setattr(H,'FollowerBus',Bus)
    monkeypatch.setattr(E,'require_restored',lambda *_:None)
    monkeypatch.setattr(R,'state_dir',lambda _:tmp_path/'state')
    monkeypatch.setattr(R,'time',NS(monotonic=clock,sleep=clock.sleep,time=lambda:1000+clock(),time_ns=lambda:int((1000+clock())*1e9)))
    monkeypatch.setattr(hooks,'ROOT',tmp_path)
    monkeypatch.setattr(A,'boot_id',lambda:'boot-test')
    return cfg,device,supervisor,tmp_path/'state',clock


def preflight(rig,**kwargs):
    cfg,device,supervisor,path,clock=rig
    supervisor.preflight(cfg,verify_registration=False,**kwargs)


@pytest.mark.parametrize('cfg',['omx1','omx2','omx3','omx4'],indirect=True)
def test_cold_start_latches_current_goals_before_enabling_all_torque(rig):
    cfg,device,s,path,clock=rig
    device.q['shoulder_pan']=.2
    preflight(rig)
    assert [event[0] for event in device.events]==['goal','enable']
    assert device.events[0][1]==device.events[1][1] and all(device.torques.values())
    assert device.goals==device.q
    assert json.loads((path/'startup_arm.json').read_text())['phase']=='ARMED'
    assert s.startup_arm_attempted and not s.rollout_started


def test_default_old_config_never_enables_torque(rig):
    cfg,device,s,path,clock=rig;cfg['recovery'].pop('startup_arm_at_home')
    with pytest.raises(R.OperatorRecoveryRequired,match='Torque OFF'):preflight(rig)
    assert not device.events and not (path/'startup_arm.json').exists()


@pytest.mark.parametrize('bad',['far_arm','far_gripper','moving','mode','mixed','hardware','serial','stopped','passive','after_rollout'])
def test_ineligible_pose_or_fault_never_writes_goals_or_torque(rig,bad):
    cfg,d,s,path,clock=rig;args={}
    if bad=='far_arm':d.q['elbow_flex']=4
    if bad=='far_gripper':d.q['gripper']=4
    if bad=='moving':
        d.q['shoulder_pan']=-.8
        d.on_read=lambda d:d.q.update(shoulder_pan=d.q['shoulder_pan']+.6)
    if bad=='mode':d.modes['wrist_roll']=3
    if bad=='mixed':d.torques['shoulder_pan']=1
    if bad=='hardware':d.errors['elbow_flex']=4
    if bad=='serial':d.read_error=OSError(5,'Input/output error')
    if bad=='stopped':s.stopped.set()
    if bad=='passive':args['allow_motion']=False
    if bad=='after_rollout':s.rollout_started=True
    with pytest.raises((RuntimeError,OSError)):preflight(rig,**args)
    assert not d.events


def test_small_arm_home_error_uses_existing_bounded_return_after_enable(rig):
    cfg,d,s,path,clock=rig;d.q['elbow_flex']=1.5
    preflight(rig)
    assert all(d.torques.values())
    assert max(abs(d.q[j]-cfg['home']['target'][j]) for j in C.JOINTS)<=cfg['home']['arrival_tolerance']
    assert d.events[0][0]=='goal' and d.events[1][0]=='enable'


@pytest.mark.parametrize('fault',['partial_enable','moved','goal_mismatch','serial_after_enable'])
def test_failed_enable_is_fenced_without_automatic_retry(rig,fault):
    cfg,d,s,path,clock=rig
    def fail(d):
        if fault=='partial_enable':d.torques['wrist_roll']=0
        if fault=='moved':d.q['shoulder_pan']=1
        if fault=='goal_mismatch':d.goals['wrist_roll']=5
        if fault=='serial_after_enable':d.read_error=OSError(5,'device lost')
    d.on_enable=fail
    with pytest.raises((RuntimeError,OSError)):preflight(rig)
    assert json.loads((path/'startup_arm.json').read_text())['phase']=='FAILED'
    before=len(d.events);d.torques={j:0 for j in C.JOINTS};d.read_error=None;d.q=dict(cfg['home']['target'])
    with pytest.raises(R.OperatorRecoveryRequired,match='Unverified startup'):preflight(rig)
    assert len(d.events)==before


def test_pending_journal_survives_supervisor_restart_and_power_cycle(rig,monkeypatch):
    cfg,d,s,path,clock=rig
    C.write_json(path/'startup_arm.json',dict(phase='ARMING',boot_id='previous-boot',device=cfg['device_id']))
    with pytest.raises(R.OperatorRecoveryRequired,match='Unverified startup'):preflight(rig)
    assert not d.events


def test_interrupted_pickup_requires_operator_reconciliation_before_arming(rig):
    cfg,d,s,path,clock=rig
    C.write_json(path/'jobs.json',{'job':{'state':'RUNNING','items':{'A':1}}})
    with pytest.raises(R.OperatorRecoveryRequired,match='Interrupted physical pickup'):preflight(rig)
    assert not d.events and json.loads((path/'startup_arm.json').read_text())['phase']=='RECONCILE_REQUIRED'
    assert json.loads((path/'jobs.json').read_text())['job']['state']=='INTERRUPTED'
    R.acknowledge_recovery(cfg)
    preflight(rig,operator_confirmed=True)
    assert [event[0] for event in d.events]==['goal','enable']
    assert any(p.name.endswith('_startup_arm.json') for p in (path/'archive').iterdir())


def test_unload_running_job_blocks_arming_before_motor_access(rig):
    cfg,d,s,path,clock=rig;cfg['role']='unload'
    C.write_json(path/'jobs.json',{'job':{'state':'RUNNING'}})
    with pytest.raises(R.OperatorRecoveryRequired,match='Interrupted job'):preflight(rig)
    assert not d.events


@pytest.mark.parametrize('key,value',[('startup_arm_at_home','yes'),('startup_arm_at_home',1),('startup_arm_tolerance',0),('startup_arm_tolerance',4),('startup_arm_tolerance',True)])
def test_bad_arming_settings_rejected(rig,key,value):
    cfg,*_=rig;cfg['recovery'][key]=value
    with pytest.raises(C.ConfigError):C.validate(cfg)


def test_new_setting_is_explicit_and_preserves_home_models_and_basket(commissioned):
    before=commissioned
    after=edit_value(before,'recovery.startup_arm_at_home','true')
    assert after['recovery']['startup_arm_at_home'] is True
    assert all(after[key]==before[key] for key in ('home','hardware','models','unload') if key in before)



def test_operator_stop_during_goal_latch_never_enables_torque(rig):
    cfg,d,s,path,clock=rig;write=d.sync_write
    def stop_after_latch(key,values):
        write(key,values);s.stopped.set()
    d.bus.sync_write=stop_after_latch
    with pytest.raises(InterruptedError,match='torque remains OFF'):preflight(rig)
    assert [event[0] for event in d.events]==['goal']
    assert not any(d.torques.values())
    assert json.loads((path/'startup_arm.json').read_text())['phase']=='FAILED'


@pytest.mark.parametrize('phase',['ARMING','FAILED','RECONCILE_REQUIRED'])
def test_unverified_journal_blocks_even_when_all_torques_remain_on(rig,phase):
    cfg,d,s,path,clock=rig;d.torques={j:1 for j in C.JOINTS}
    C.write_json(path/'startup_arm.json',dict(phase=phase,device=cfg['device_id']))
    with pytest.raises(R.OperatorRecoveryRequired,match='Unverified startup'):preflight(rig)
    assert not d.events


def test_previous_success_allows_next_cold_start_but_rechecks_current_pose(rig):
    cfg,d,s,path,clock=rig
    C.write_json(path/'startup_arm.json',dict(phase='ARMED',device=cfg['device_id'],boot_id='old'))
    preflight(rig)
    assert [event[0] for event in d.events]==['goal','enable']


def test_pose_changed_after_stability_samples_never_writes(rig):
    cfg,d,s,path,clock=rig;reads=0
    def move_on_arm(d):
        nonlocal reads
        reads+=1
        if reads==5:d.q['shoulder_pan']=3
    d.on_read=move_on_arm
    with pytest.raises(R.OperatorRecoveryRequired,match='Outside startup Home'):preflight(rig)
    assert not d.events and json.loads((path/'startup_arm.json').read_text())['phase']=='FAILED'


def test_slow_feedback_never_enables_torque(rig):
    cfg,d,s,path,clock=rig
    d.on_read=lambda _:clock.sleep(cfg['home']['max_sample_gap_s']+1)
    with pytest.raises(R.OperatorRecoveryRequired,match='feedback gap'):preflight(rig)
    assert not d.events
