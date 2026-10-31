"""Offline regressions for interrupted jobs, inspection recovery and final hold."""
import ast,copy,json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest
from omx_manager import config as C,runtime as R,hardware as H,basket
from omx_manager.simulation import Clock
from test_basket_registration import registered_config,scene
from test_low_exposure import setup,reference
from omx_manager import exposure as E

ROOT=Path(__file__).resolve().parents[1]


def load_function(path,name,cls=None,namespace=None):
 tree=ast.parse((ROOT/path).read_text());body=tree.body
 if cls:body=next(n for n in body if isinstance(n,ast.ClassDef) and n.name==cls).body
 node=copy.deepcopy(next(n for n in body if isinstance(n,ast.FunctionDef) and n.name==name));node.decorator_list=[]
 env=dict(namespace or {});exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),path,'exec'),env)
 return env[name]


def test_interrupted_pickup_is_failed_once_without_replay_or_duplicate_id_loss(tmp_path,monkeypatch):
 cfg=C.load(ROOT/'params/omx2.yaml');monkeypatch.setattr(R,'state_dir',lambda _:tmp_path)
 jobs={'same-id':dict(state='RUNNING',items={'D':2},success=None)}
 C.write_json(tmp_path/'jobs.json',jobs)
 C.write_json(tmp_path/'inventory.json',dict(capacity=8,remaining=1,total_completed=7,order_completed=1,order_quantity=2))
 assert R.settle_interrupted_pickup(cfg)
 archived=json.loads(next((tmp_path/'archive').iterdir()).read_text())
 assert archived['inventory']['order_completed']==1 and archived['jobs']==jobs
 row=json.loads((tmp_path/'jobs.json').read_text())['same-id']
 assert row['state']=='INTERRUPTED' and row['success'] is False
 assert json.loads((tmp_path/'inventory.json').read_text())['order_item']=='WAIT'
 assert not R.settle_interrupted_pickup(cfg)
 assert len(list((tmp_path/'archive').iterdir()))==1


def test_manual_interruption_policy_and_corrupt_stock_are_never_silently_cleared(tmp_path,monkeypatch):
 cfg=C.load(ROOT/'params/omx1.yaml');monkeypatch.setattr(R,'state_dir',lambda _:tmp_path)
 C.write_json(tmp_path/'jobs.json',{'x':{'state':'RUNNING'}})
 cfg['recovery']['interrupted_order_action']='require_ack'
 assert not R.settle_interrupted_pickup(cfg)
 cfg['recovery']['interrupted_order_action']='fail_and_idle'
 C.write_json(tmp_path/'inventory.json',dict(capacity=8,remaining=2,total_completed=8))
 before=(tmp_path/'jobs.json').read_bytes()
 with pytest.raises(R.OperatorRecoveryRequired):R.settle_interrupted_pickup(cfg)
 assert (tmp_path/'jobs.json').read_bytes()==before


def test_preflight_still_refuses_torque_off_after_archiving_order(tmp_path,monkeypatch):
 cfg=C.load(ROOT/'params/omx1.yaml');cfg['home']['target']={j:0 for j in C.JOINTS}
 monkeypatch.setattr(R,'state_dir',lambda _:tmp_path)
 monkeypatch.setattr(E,'require_restored',lambda _:None)
 bus=NS(read=lambda key,j:0 if key=='Torque_Enable' else 5 if j=='gripper' else 4)
 class Device:
  def __init__(self,_):self.bus=bus
  def __enter__(self):return self
  def __exit__(self,*_):pass
  def read(self):return {j:0 for j in C.JOINTS}
 monkeypatch.setattr(H,'FollowerBus',Device)
 C.write_json(tmp_path/'jobs.json',{'x':{'state':'RUNNING'}})
 with pytest.raises(R.OperatorRecoveryRequired,match='Torque OFF'):
  R.Supervisor('cfg').preflight(cfg,verify_registration=False)


def test_camera_recovery_is_bounded_and_can_be_disabled():
 cfg=C.load(ROOT/'params/omx2.yaml');args,*_=R.build(cfg,'cfg')
 assert '--strategy.camera_auto_reconnect=true' in args and '--strategy.camera_reconnect_attempts=2' in args
 cfg['runtime']['camera_auto_reconnect']=False
 assert '--strategy.camera_auto_reconnect=false' in R.build(cfg,'cfg')[0]
 cfg['runtime']['camera_reconnect_attempts']=99
 with pytest.raises(C.ConfigError):C.validate(cfg)


def test_shutdown_latches_arm_without_touching_gripper_or_modes():
 write=Mock();q={j:1. for j in C.JOINTS}
 robot=NS(bus=NS(is_connected=True,read=lambda *_:4,sync_write=write,sync_read=lambda *a,**kw:q),
          _check_motor_health=Mock(),calibration={j:NS(range_min=-100,range_max=100) for j in C.JOINTS})
 fn=load_function('lerobot_unified/src/lerobot/robots/omx_follower/omx_follower.py','hold_current_position','OmxFollower',dict(logger=Mock()))
 fn(robot)
 register,targets=write.call_args.args
 assert register=='Goal_Position' and set(targets)==set(C.JOINTS)-{'gripper'}
 robot._check_motor_health.side_effect=RuntimeError('Torque OFF')
 write.reset_mock()
 with pytest.raises(RuntimeError):fn(robot)
 write.assert_not_called()


def test_cleanup_still_closes_port_if_final_hold_fails():
 bus=Mock(is_connected=True);robot=NS(cameras={},bus=bus,config=NS(disable_torque_on_disconnect=False),
                                     hold_current_position=Mock(side_effect=RuntimeError('USB disconnected')))
 result=H.cleanup_resources(robot=robot)
 assert any('motor_hold' in item for item in result)
 bus.disconnect.assert_called_once()


def test_read_only_motor_probe_identifies_static_goal_with_oscillating_feedback():
 from omx_manager.motor_diagnostics import collect
 clock=Clock();cfg=C.load(ROOT/'params/omx2.yaml');n=0
 def read():
  nonlocal n;n+=1
  return {j:(.4 if n%2 else -.4) if j=='wrist_roll' else 0. for j in C.JOINTS}
 bus=NS(sync_read=lambda _:{j:0. for j in C.JOINTS},read=lambda *_:1)
 result=collect(NS(read=read,bus=bus),cfg,samples=5,clock=clock,sleep=clock.sleep)
 assert result['motor_writes'] is False
 assert result['joints']['wrist_roll']['position_span']==.8 and result['joints']['wrist_roll']['goal_span']==0


def test_registered_temporal_median_rejects_one_frame_noise_and_detects_real_object(tmp_path):
 cfg=registered_config(tmp_path);inspector=basket.BasketInspector(cfg);empty,_=scene()
 occupied,_=scene(item=(55,55,14,25))
 assert inspector.update(empty,1,1)=='UNKNOWN'
 assert inspector.update(empty,1.11,1.11)=='UNKNOWN'
 for i in range(5):result=inspector.update(empty,1.22+i*.11,1.22+i*.11)
 assert result=='EMPTY'
 # One transient is filtered; a persistent object must not be hidden.
 assert inspector.update(occupied,1.77,1.77)=='EMPTY'
 for i in range(7):result=inspector.update(occupied,1.88+i*.11,1.88+i*.11)
 assert result=='OCCUPIED'
 assert inspector.update(empty,10,9)=='UNKNOWN'
 assert inspector.temporal_frames==[]


def test_slow_control_read_cannot_age_the_frame_before_classification(setup,tmp_path,monkeypatch):
 cfg,clock,controls,camera=setup;camera.shape=(480,640,3);reference(cfg,tmp_path);inspector=basket.BasketInspector(cfg)
 original=E.Session.verify
 def slow(self):clock.sleep(.35);return original(self)
 monkeypatch.setattr(E.Session,'verify',slow)
 cfg['unload']['inspection_control_poll_s']=1.;inspector=basket.BasketInspector(cfg)
 assert E.inspect(camera,cfg,inspector)=='EMPTY'
 assert not E.journal_path(cfg).exists()


def test_uncertain_inspection_restores_before_recoverable_exception(setup,tmp_path,monkeypatch):
 cfg,clock,controls,camera=setup;reference(cfg,tmp_path);inspector=basket.BasketInspector(cfg)
 inspector.update=lambda *args:'UNKNOWN'
 with pytest.raises(E.InspectionUncertain,match='reason'):
  E.inspect(camera,cfg,inspector)
 assert controls.values==controls.initial and not E.journal_path(cfg).exists()


def test_failed_restoration_is_fatal_and_never_the_recoverable_inspection_type(setup,tmp_path):
 cfg,clock,controls,camera=setup;reference(cfg,tmp_path);inspector=basket.BasketInspector(cfg)
 inspector.update=lambda *args:'UNKNOWN';controls.fail_restore=True
 with pytest.raises(RuntimeError,match='restoration unverified') as error:E.inspect(camera,cfg,inspector)
 assert not isinstance(error.value,E.InspectionUncertain) and E.journal_path(cfg).exists()


def test_uncertain_job_keeps_controller_loaded_and_next_new_job_can_finish(tmp_path,monkeypatch):
 import importlib.util,sys
 from omx_manager import workstation as W
 ipcpath=ROOT/'lerobot_unified/src/lerobot/rollout/workstation_ipc.py'
 spec=importlib.util.spec_from_file_location('lerobot.rollout.workstation_ipc',ipcpath)
 ipc=importlib.util.module_from_spec(spec);spec.loader.exec_module(ipc)
 monkeypatch.setitem(sys.modules,'lerobot.rollout.workstation_ipc',ipc)
 cfg=C.load(ROOT/'params/omx4.yaml');cfg['home']['target']={j:0. for j in C.JOINTS}
 options=dict(basket.DEFAULTS,low_exposure_enabled=True,job_timeout_s=60.)
 inspector=NS(spec=options,reset=Mock(),last_source_frame=np.full((48,64,3),100,np.uint8),last_source_stamp=0.)
 monkeypatch.setattr(W,'BasketInspector',lambda _:inspector)
 monkeypatch.setattr(W,'initialize_home',lambda *a,**kw:None)
 monkeypatch.setattr(W,'heartbeat',lambda:None)
 clock=Clock();stopped=False;statuses=[]
 monkeypatch.setattr(W,'time',NS(perf_counter=clock,time=lambda:1000+clock()))
 monkeypatch.setenv('OMX_WORKSTATION_SESSION','one-session')
 command=tmp_path/'command.json';status=tmp_path/'status.json'
 C.write_json(command,dict(session='one-session',command='START',job_id='failed-job',expected_count=None))
 original=ipc.atomic_json
 def write(path,row):
  nonlocal stopped
  statuses.append(row.copy());original(path,row)
  if row['phase']=='INSPECTION_FAILED':
   C.write_json(command,dict(session='one-session',command='CLEAR',job_id=row['job_id']))
  elif row['phase']=='READY' and any(s['phase']=='INSPECTION_FAILED' for s in statuses):
   C.write_json(command,dict(session='one-session',command='START',job_id='new-job',expected_count=None))
  elif row['phase']=='DONE':stopped=True
 monkeypatch.setattr(ipc,'atomic_json',write)
 inspections=[]
 def inspect(camera,cfg,inspector,**kwargs):
  inspections.append(True)
  if len(inspections)==1:
   kwargs['report'](dict(verdict='UNKNOWN',candidate='UNKNOWN',reason='REGISTERED_FAINT_CHANGE',at=1000.))
   raise E.InspectionUncertain('faint frame')
  return 'EMPTY'
 monkeypatch.setattr(E,'inspect',inspect)
 def wait(dt):clock.sleep(dt)
 stop=NS(is_set=lambda:stopped or clock()>5,wait=wait)
 inner=NS(cameras={'top':object()},get_joint_observation=lambda:cfg['home']['target'])
 strategy=NS(_manager_home=NS(cfg=cfg),_engine=NS(pause=Mock()),reset_control_state=Mock(),
             config=NS(workstation_control_file=str(command),workstation_status_file=str(status),workstation_poll_s=.01),
             _interpolator=NS(multiplier=1))
 ctx=NS(hardware=NS(robot_wrapper=NS(inner=inner)),runtime=NS(shutdown_event=stop,cfg=NS(fps=30),cadence_report=None))
 W.run(strategy,ctx,Mock(side_effect=AssertionError('No model actions')),lambda *a,**kw:object())
 assert [s['phase'] for s in statuses]==['READY','INSPECTING','INSPECTION_FAILED','READY','INSPECTING','DONE']
 assert statuses[-1]['job_id']=='new-job' and statuses[-1]['estimated_transfers']==0
 failure=json.loads((tmp_path/'basket_failure.json').read_text())
 assert failure['job_id']=='failed-job' and failure['restore_verified'] is True
 assert failure['reason']=='REGISTERED_FAINT_CHANGE'
 assert Path(failure['image_path']).is_file()
 assert failure['image_at']<=failure['failure_at']


def test_mqtt_worker_fails_uncertain_job_then_releases_for_new_order(tmp_path):
 import os,time
 state={'session':'s','phase':'READY','pid':123};commands=[]
 def write(path,row):
  commands.append(row['command'])
  state.update(phase='INSPECTION_FAILED',job_id='failed',restore_verified=True,message='uncertain') if row['command']=='START' else state.update(phase='READY')
 fn=load_function('lerobot_unified/omx_mqtt_client.py','execute_unload',namespace=dict(
  robot_id='omx4',WORKSTATION_IDS=('omx3','omx4'),workstation_session='s',workstation_command=tmp_path/'command',
  workstation_ready=lambda:state['phase']=='READY',workstation_state=lambda:state,
  _workstation_process_alive=lambda _:True,atomic_json=write,os=os,time=time))
 with pytest.raises(RuntimeError,match='idle restored'):fn('failed',{'F':1})
 assert commands==['START','CLEAR'] and state['phase']=='READY'


def test_old_empty_snapshot_is_diagnostic_only_never_current_empty(tmp_path,monkeypatch):
 from omx_manager import cli
 cfg=C.load(ROOT/'params/omx4.yaml');monkeypatch.setattr(cli,'load',lambda _:cfg)
 monkeypatch.setattr(cli,'state_dir',lambda _:tmp_path)
 C.write_json(tmp_path/'basket_status.json',dict(verdict='EMPTY',at=1.,reason='CONFIRMED'))
 result=cli.invoke('cfg','basket-status')
 assert result['verdict']=='UNKNOWN' and result['last_snapshot']['verdict']=='EMPTY'
