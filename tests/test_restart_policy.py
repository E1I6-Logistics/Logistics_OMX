"""SDK-free restart faults, power-cut reconciliation and recovery state machine."""
import copy
import json
import os
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from omx_manager import config as C,runtime as R,restart_policy as P,faults as F,commissioning
from omx_manager.journal import JobJournal
from test_manager import cfg,commissioned
from test_startup_arming import rig
from test_runtime_reliability import load_function


@pytest.mark.parametrize('error,code',[
    (OSError(5,'Input/output error'),'TRANSPORT'),
    (ConnectionError('port unavailable'),'TRANSPORT'),
    (ConnectionError('Failed to open OpenCVCamera(/dev/video0)'),'CAMERA'),
    (RuntimeError('Motor hardware error: elbow_flex=4'),'MOTOR_FAULT'),
    (RuntimeError('mode mismatch'),'MOTOR_FAULT'),
    (RuntimeError('Torque OFF'),'TORQUE_OFF'),
    (C.ConfigError('No unique tested return route'),'POSE_UNKNOWN'),
    (TimeoutError('Home movement timed out'),'HOME_FOLLOWING'),
    (C.ConfigError('Camera exposure restoration pending'),'CAMERA_RESTORE'),
    (MemoryError('out of memory'),'RESOURCE'),
    (RuntimeError('OMX workstation job timeout'),'JOB_TIMEOUT'),
    (C.ConfigError('invalid camera keys'),'CONFIGURATION'),
    (RuntimeError('unexpected model tensor shape'),'UNKNOWN')])
def test_fault_category_does_not_hide_nonrecoverable_conditions(error,code):
    assert F.classify(error)==code
    wrapped=RuntimeError('rollout wrapper');wrapped.__cause__=error
    assert F.classify(wrapped)==code


@pytest.mark.parametrize('cfg',['omx1','omx2','omx3','omx4'],indirect=True)
def test_restart_discards_active_work_but_preserves_failed_ids_and_evidence(cfg,tmp_path):
    cfg['recovery']['restart_to_idle']=True;before=copy.deepcopy(cfg)
    journal=JobJournal(tmp_path/'jobs.json');journal.begin('old',{'A':2});journal.begin('done',{'A':1});journal.finish('done',True,'completed')
    for name in ('workstation_command.json','workstation_status.json','unload_progress.json'):
        C.write_json(tmp_path/name,dict(session='old',phase='RUNNING',command='START',current=1))
    C.write_json(tmp_path/'inventory.json',dict(capacity=8,total_completed=7,remaining=1,order_completed=1))
    assert P.reconcile(cfg,tmp_path)
    assert journal.lookup('old',{'A':2})['state']=='INTERRUPTED'
    assert journal.lookup('old',{'A':2})['success'] is False
    assert journal.lookup('done',{'A':1})['success'] is True
    with pytest.raises(ValueError,match='replay'):journal.begin('old',{'A':2})
    assert not (tmp_path/'inventory.json').exists() and not (tmp_path/'workstation_command.json').exists()
    assert json.loads(next((tmp_path/'archive').glob('restart_*/unload_progress.json')).read_text())['current']==1
    assert not P.reconcile(cfg,tmp_path)
    assert cfg==before


def test_power_cut_after_intent_finishes_same_reset_without_duplicate_archive(cfg,tmp_path,monkeypatch):
    cfg['recovery']['restart_to_idle']=True
    C.write_json(tmp_path/'jobs.json',{'x':{'state':'RUNNING'}})
    original=P.write_json
    def power_cut(path,row,**kwargs):
        if path==tmp_path/'jobs.json':raise OSError('power cut after intent')
        return original(path,row,**kwargs)
    monkeypatch.setattr(P,'write_json',power_cut)
    with pytest.raises(OSError):P.reconcile(cfg,tmp_path)
    assert (tmp_path/'idle_reset_pending.json').exists()
    monkeypatch.setattr(P,'write_json',original)
    assert P.reconcile(cfg,tmp_path)
    assert len(list((tmp_path/'archive').iterdir()))==1
    assert not (tmp_path/'idle_reset_pending.json').exists()


def test_corrupt_state_is_never_cleared_as_if_successful(cfg,tmp_path):
    cfg['recovery']['restart_to_idle']=True
    C.write_json(tmp_path/'jobs.json',{'x':'broken'})
    before=(tmp_path/'jobs.json').read_bytes()
    with pytest.raises(C.ConfigError):P.reconcile(cfg,tmp_path)
    assert (tmp_path/'jobs.json').read_bytes()==before and not (tmp_path/'archive').exists()


def test_opt_in_reset_then_startup_arm_works_for_interrupted_order(rig):
    cfg,d,s,path,clock=rig;cfg['recovery']['restart_to_idle']=True
    journal=JobJournal(path/'jobs.json');journal.begin('old',{'A':1})
    s.preflight(cfg,verify_registration=False)
    assert all(d.torques.values()) and journal.lookup('old',{'A':1})['state']=='INTERRUPTED'
    assert not s.startup_arm_reconcile_required


def test_persistent_budget_cannot_be_reset_by_supervisor_restart(cfg,tmp_path,monkeypatch):
    cfg['recovery']['restart_to_idle']=True;monkeypatch.setattr(P,'boot_id',lambda:'one-boot')
    for i in range(3):assert P.budget(cfg,tmp_path,consume='CAMERA')==i+1
    with pytest.raises(C.ConfigError,match='limit reached'):P.budget(cfg,tmp_path)
    monkeypatch.setattr(P,'boot_id',lambda:'next-boot')
    assert P.budget(cfg,tmp_path)==0


@pytest.fixture
def wait_rig(cfg,tmp_path,monkeypatch):
    cfg['recovery']['restart_to_idle']=True;cfg['recovery']['poll_s']=1
    path=tmp_path/'cfg.yaml';C.save(path,cfg)
    monkeypatch.setattr(R,'state_dir',lambda _:tmp_path/'state')
    monkeypatch.setattr(R,'check_files',lambda _:None)
    monkeypatch.setattr(commissioning,'require_commissioned',lambda _:None)
    monkeypatch.setattr(P,'boot_id',lambda:'test-boot')
    monkeypatch.setattr(R.subprocess,'Popen',lambda *a,**k:NS(pid=123,poll=lambda:None))
    closed=Mock();monkeypatch.setattr(R,'stop_process',closed)
    class Event:
        count=0
        def is_set(self):return self.count>=5
        def wait(self,dt):self.count+=1;return self.is_set()
    with (tmp_path/'device.lock').open('w') as lease_fd:
        def supervisor():
            s=R.Supervisor(path);s.stopped=Event();s.preflight=Mock();s.device_lease=NS(fd=lease_fd);return s
        yield cfg,tmp_path/'state',supervisor,closed


@pytest.mark.parametrize('code',['TRANSPORT','CAMERA','WATCHDOG','MQTT','PROCESS_EXIT','RESOURCE','JOB_TIMEOUT'])
def test_transient_retry_requires_two_healthy_preflights_then_reaps_presence(wait_rig,code):
    cfg,path,factory,closed=wait_rig;s=factory()
    s.wait_for_recovery(cfg,F.ManagedFault('test fault',code))
    assert s.preflight.call_count==2 and all(c.kwargs['allow_motion'] for c in s.preflight.call_args_list)
    assert s.retry_reserved and json.loads((path/'auto_restart.json').read_text())['attempts']==1
    closed.assert_called_once()


def test_same_fault_eventually_stays_in_wait_across_new_supervisors(wait_rig):
    cfg,path,factory,closed=wait_rig
    for _ in range(3):
        s=factory();s.wait_for_recovery(cfg,F.ManagedFault('USB lost','TRANSPORT'));assert s.retry_reserved
    s=factory();s.wait_for_recovery(cfg,F.ManagedFault('USB lost','TRANSPORT'))
    s.preflight.assert_not_called();assert not s.retry_reserved
    assert 'limit reached' in json.loads((path/'recovery_wait.json').read_text())['reason']


@pytest.mark.parametrize('code',['MOTOR_FAULT','TORQUE_OFF','POSE_UNKNOWN','HOME_FOLLOWING','CONFIGURATION','UNKNOWN'])
def test_fatal_fault_never_authorizes_auto_motion_or_model_reload(wait_rig,code):
    cfg,path,factory,closed=wait_rig;s=factory()
    s.wait_for_recovery(cfg,F.ManagedFault('test fault',code))
    s.preflight.assert_not_called();assert not s.retry_reserved


def test_failed_sensor_restoration_is_attempted_once_with_no_model_retry(wait_rig):
    cfg,path,factory,closed=wait_rig;s=factory()
    s.restore_sensor=Mock(side_effect=RuntimeError('sensor unavailable'))
    s.preflight=Mock(side_effect=C.ConfigError('Camera exposure restoration pending'))
    s.wait_for_recovery(cfg,C.ConfigError('Camera exposure restoration pending'))
    s.restore_sensor.assert_called_once();assert not s.retry_reserved


@pytest.mark.parametrize('error',[C.ConfigError('No unique tested return route'),TimeoutError('Home movement timed out'),R.OperatorRecoveryRequired('Torque OFF')])
def test_nonmanaged_fatal_exceptions_cannot_use_legacy_auto_resume(wait_rig,error):
    cfg,path,factory,closed=wait_rig;s=factory()
    s.wait_for_recovery(cfg,error)
    s.preflight.assert_not_called();assert not s.retry_reserved


def test_stop_during_sensor_recovery_reaps_its_process(wait_rig):
    cfg,path,factory,closed=wait_rig;s=factory()
    with pytest.raises(InterruptedError):s.restore_sensor(cfg)
    closed.assert_called_once();assert not s.pending_cleanup


def test_sensor_cleanup_failure_quarantines_child_before_new_hardware(wait_rig):
    cfg,path,factory,closed=wait_rig;s=factory();closed.side_effect=TimeoutError('unreaped')
    with pytest.raises(TimeoutError,match='unreaped'):s.restore_sensor(cfg)
    assert len(s.pending_cleanup)==1
    with pytest.raises(RuntimeError,match='new hardware session blocked'):s.reap_pending(cfg)


def test_preflight_motor_fault_stops_automatic_retry_even_after_transport_fault(wait_rig):
    cfg,path,factory,closed=wait_rig;s=factory()
    s.preflight=Mock(side_effect=RuntimeError('Motor hardware error: elbow_flex=4'))
    s.wait_for_recovery(cfg,F.ManagedFault('USB lost','TRANSPORT'))
    s.preflight.assert_called_once();assert not s.retry_reserved


def test_pending_restore_discovered_after_child_exit_is_handled_before_model(wait_rig):
    cfg,path,factory,closed=wait_rig;s=factory();s.restore_sensor=Mock()
    s.preflight=Mock(side_effect=[C.ConfigError('Camera exposure restoration pending'),None,None])
    s.wait_for_recovery(cfg,F.ManagedFault('worker exited','PROCESS_EXIT'))
    s.restore_sensor.assert_called_once();assert s.preflight.call_count==3 and s.retry_reserved


def test_last_reserved_retry_passes_preflight_but_next_supervisor_is_fenced(rig,monkeypatch):
    cfg,d,s,path,clock=rig;cfg['recovery']['restart_to_idle']=True
    monkeypatch.setattr(P,'boot_id',lambda:'test-boot')
    for _ in range(3):P.budget(cfg,path,consume='CAMERA')
    s.retry_reserved=True;s.preflight(cfg,verify_registration=False)
    fresh=R.Supervisor('cfg')
    with pytest.raises(C.ConfigError,match='limit reached'):fresh.preflight(cfg,verify_registration=False)


def test_sensor_child_inherits_device_lease_and_is_reaped_before_unlock(wait_rig,monkeypatch):
    cfg,path,factory,closed=wait_rig;s=factory();created=Mock(return_value=NS(pid=123,poll=lambda:None))
    monkeypatch.setattr(R.subprocess,'Popen',created)
    with pytest.raises(InterruptedError):s.restore_sensor(cfg)
    assert created.call_args.kwargs['pass_fds']==(s.device_lease.fd.fileno(),)
    assert '--lease-fd' in created.call_args.args[0]
    closed.assert_called_once()


def test_child_reports_are_tagged_and_stale_sessions_cannot_classify_new_worker(tmp_path,monkeypatch):
    path=tmp_path/'fault.json';monkeypatch.setenv('OMX_ROLLOUT_FAULT',str(path));monkeypatch.setenv('OMX_SESSION','new')
    F.record_rollout_fault(RuntimeError('Motor hardware error: fault'))
    assert F.worker_failure(path,os.getpid(),'new','exit').fault_code=='MOTOR_FAULT'
    assert F.worker_failure(path,os.getpid(),'old','exit').fault_code=='PROCESS_EXIT'


def test_mqtt_connection_report_is_tagged_with_actual_callback_state(tmp_path,monkeypatch):
    path=tmp_path/'mqtt.json';monkeypatch.setenv('OMX_MQTT_STATE',str(path));monkeypatch.setenv('OMX_SESSION','one')
    fn=load_function('lerobot_unified/omx_mqtt_client.py','record_connection',namespace=dict(os=os,time=NS(time=lambda:1),atomic_json=C.write_json))
    fn(True);assert json.loads(path.read_text())==dict(connected=True,pid=os.getpid(),session='one',at=1)
    fn(False);assert json.loads(path.read_text())['connected'] is False


@pytest.mark.parametrize('key,value',[('restart_to_idle','yes'),('restart_to_idle',1),('auto_retry_limit',0),('auto_retry_limit',6),('auto_retry_limit',True)])
def test_bad_automatic_restart_config_rejected(cfg,key,value):
    cfg['recovery'][key]=value
    with pytest.raises(C.ConfigError):C.validate(cfg)
