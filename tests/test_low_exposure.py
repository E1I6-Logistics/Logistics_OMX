"""Fake cameras exercise real transactions; no model, sensors or broker used."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from threading import Lock

import cv2
import numpy as np
import pytest

from omx_manager import config as C,exposure as E,basket,commissioning
from omx_manager.simulation import Clock
from omx_manager.rgb_roi import signature
from omx_manager.workflow import registration_binding

ROOT=Path(__file__).resolve().parents[1]


class Controls:
    backend='opencv-v4l2';manual=1.
    def __init__(self):
        self.values=dict(auto_exposure=3.,exposure=400.,gain=20.,auto_white_balance=1.,white_balance=4600.)
        self.initial=self.values.copy();self.writes=[];self.fail_restore=False;self.ignore_low=False
    def snapshot(self):return self.values.copy()
    def get(self,key):return self.values[key]
    def set(self,key,value):
        self.writes.append((key,value))
        if self.fail_restore and key=='exposure' and value==400.:raise RuntimeError('USB restore write failed')
        if self.ignore_low and key=='exposure' and value==100.:return
        self.values[key]=value
    def ranges(self):return dict(min=1,max=1000,step=1)


class Camera:
    def __init__(self,clock,controls):
        self.clock=clock;self._omx_test_controls=controls;self.is_connected=True
        self.connect=Mock();self.disconnect=Mock();self.frozen=False;self.reads=[]
        self.shape=(48,64,3)
    def read_latest_with_timestamp(self,**kwargs):
        stamp=int(self.clock()*30)/30 if not self.frozen else 0.
        value=180 if self._omx_test_controls.values['auto_exposure']==3 else 80
        self.reads.append((stamp,value))
        return np.full(self.shape,value,np.uint8),stamp


@pytest.fixture
def setup(tmp_path,monkeypatch):
    cfg=commissioning.tune(C.load(ROOT/'params/omx4.yaml'))
    cfg['unload'].update(low_exposure_enabled=True,inspection_exposure=100.,vision_method='low_template',roi=[0,0,640,480],full_view_confirmed=True)
    clock=Clock();controls=Controls();camera=Camera(clock,controls)
    monkeypatch.setattr(E,'ROOT',tmp_path)
    monkeypatch.setattr(E,'time',NS(perf_counter=clock,sleep=clock.sleep,time=lambda:1000+clock()))
    monkeypatch.setattr(basket,'time',NS(perf_counter=clock))
    monkeypatch.setattr('omx_manager.hooks.heartbeat',lambda *a,**kw:None)
    return cfg,clock,controls,camera


def test_transaction_restores_settings_and_normal_frames_before_policy_dispatch(setup):
    cfg,clock,controls,camera=setup;robot=NS(cameras={'top':camera},_omx_manager_snapshot=cfg)
    before=copy.deepcopy(cfg['hardware'])
    with E.Session(camera,cfg) as session:
        assert controls.values['exposure']==100 and controls.values['auto_exposure']==1
        assert E.journal_path(cfg).exists()
        with pytest.raises(RuntimeError,match='blocked'):E.ensure_policy_ready(robot)
        assert camera.reads[-1][1]==80
    assert controls.values==controls.initial and camera.reads[-1][1]==180
    assert not E.journal_path(cfg).exists() and camera._omx_exposure_blocked is False
    E.ensure_policy_ready(robot)
    assert cfg['hardware']==before and session.timing['exposure_restore_s']>0


@pytest.mark.parametrize('error',[RuntimeError,KeyboardInterrupt])
def test_failure_and_operator_interrupt_still_restore_original_controls(setup,error):
    cfg,clock,controls,camera=setup
    with pytest.raises(error):
        with E.Session(camera,cfg):raise error('stop test')
    assert controls.values==controls.initial and not E.journal_path(cfg).exists()


def test_restore_failure_preserves_durable_journal_and_blocks_new_session(setup):
    cfg,clock,controls,camera=setup
    with pytest.raises(RuntimeError,match='restoration unverified'):
        with E.Session(camera,cfg):controls.fail_restore=True
    data=json.loads(E.journal_path(cfg).read_text())
    assert data['original']==controls.initial and data['phase']=='RESTORE_REQUIRED'
    assert camera._omx_exposure_blocked is True
    with pytest.raises(C.ConfigError,match='restoration pending'):E.require_restored(cfg)
    controls.fail_restore=False
    assert E.recover(camera,cfg)['phase']=='RESTORED'
    assert not E.journal_path(cfg).exists() and controls.values==controls.initial


def test_ignored_low_exposure_is_detected_then_restored(setup):
    cfg,clock,controls,camera=setup;controls.ignore_low=True
    with pytest.raises(C.ConfigError,match='setting changed') as caught:
        with E.Session(camera,cfg):pytest.fail('Ignored control must not be accepted')
    message=str(caught.value)
    assert 'exposure requested=100 actual=400' in message
    assert 'backend=opencv-v4l2' in message and 'auto_exposure=1' in message
    assert controls.values==controls.initial and not E.journal_path(cfg).exists()


def test_stale_frames_fail_before_any_sensor_changes(setup):
    cfg,clock,controls,camera=setup;camera.frozen=True
    with pytest.raises(TimeoutError):
        with E.Session(camera,cfg):pytest.fail('Frozen stream')
    assert not controls.writes and not E.journal_path(cfg).exists()


def test_operator_stop_before_entry_performs_no_changes(setup):
    cfg,clock,controls,camera=setup
    with pytest.raises(InterruptedError):
        with E.Session(camera,cfg,stopped=lambda:True):pass
    assert not controls.writes


def test_new_process_cannot_send_policy_actions_while_journal_remains(setup):
    cfg,clock,controls,camera=setup
    C.write_json(E.journal_path(cfg),{'phase':'PENDING'})
    with pytest.raises(C.ConfigError,match='restoration pending'):
        E.ensure_policy_ready(NS(cameras={},_omx_manager_snapshot=cfg))


def test_recovery_refuses_different_camera_identity(setup):
    cfg,clock,controls,camera=setup
    with pytest.raises(RuntimeError):
        with E.Session(camera,cfg):controls.fail_restore=True
    controls.fail_restore=False;cfg['hardware']['cameras']['top']['index_or_path']='another-camera'
    with pytest.raises(C.ConfigError,match='identity changed'):E.recover(camera,cfg)
    assert E.journal_path(cfg).exists()


def test_opencv_adapter_readback_and_capture_lock():
    props=dict(auto_exposure=cv2.CAP_PROP_AUTO_EXPOSURE,exposure=cv2.CAP_PROP_EXPOSURE,gain=cv2.CAP_PROP_GAIN,
               auto_white_balance=cv2.CAP_PROP_AUTO_WB,white_balance=cv2.CAP_PROP_WB_TEMPERATURE)
    controls=Controls();data={props[k]:v for k,v in controls.values.items()};data[cv2.CAP_PROP_MODE]=0
    cap=NS(getBackendName=lambda:'V4L2',get=lambda key:data.get(key,-1),set=lambda key,value:(data.__setitem__(key,value) or True))
    camera=NS(videocapture=cap,control_lock=Lock());adapter=E.OpenCVControls(camera)
    assert adapter.snapshot()==controls.initial
    adapter.set('exposure',100);assert data[cv2.CAP_PROP_EXPOSURE]==100
    cap.set=lambda *a:True
    with pytest.raises(C.ConfigError,match='clamped/ignored'):adapter.set('exposure',50)
    data[cv2.CAP_PROP_MODE]=1
    with pytest.raises(C.ConfigError,match='Normalized'):E.OpenCVControls(camera)


def test_realsense_adapter_touches_rgb_controls_only(monkeypatch):
    import sys
    controls=Controls();controls.manual=0.;controls.values['auto_exposure']=1.
    sensor=NS(supports=lambda option:True,get_option=lambda option:controls.get(option),
              set_option=lambda option,value:controls.set(option,value),get_option_range=lambda option:NS(min=1,max=1000,step=1))
    options=NS(enable_auto_exposure='auto_exposure',exposure='exposure',gain='gain',enable_auto_white_balance='auto_white_balance',white_balance='white_balance')
    monkeypatch.setitem(sys.modules,'pyrealsense2',NS(option=options))
    adapter=E.RealSenseControls(NS(_get_color_sensor=lambda:sensor))
    original=adapter.snapshot();E.apply(adapter,dict(original,auto_exposure=0.,exposure=100.,auto_white_balance=0.))
    E.restore_controls(adapter,original)
    assert controls.values==original and all(k in original for k,v in controls.writes)


def reference(cfg,tmp_path,profile=True):
    if cfg['home']['target'] is None:cfg['home']['target']={j:0. for j in C.JOINTS}
    spec=basket.options(cfg);path=tmp_path/'low_empty.npz'
    fields=dict(image=np.full((240,320,3),80,np.uint8),roi=spec['roi'],roi_signature=signature(spec))
    if profile:fields['exposure_profile']=json.dumps(dict(backend='opencv-v4l2',controls=dict(Controls().initial,auto_exposure=1.,exposure=100.,auto_white_balance=0.)))
    np.savez_compressed(path,**fields);cfg['unload'].update(reference=str(path),reference_binding=registration_binding(cfg))
    return cfg


def test_low_reference_binding_and_same_camera_controls_are_required(setup,tmp_path):
    cfg,*_=setup;reference(cfg,tmp_path,profile=False)
    with pytest.raises(C.ConfigError,match='new low-exposure'):basket.BasketInspector(cfg)
    reference(cfg,tmp_path);inspector=basket.BasketInspector(cfg)
    assert inspector.exposure_profile['controls']['gain']==20
    cfg['unload']['inspection_exposure']=50.
    with pytest.raises(C.ConfigError,match='changed'):basket.BasketInspector(cfg)


def test_low_reference_cannot_be_reused_for_normal_camera(setup,tmp_path):
    cfg,*_=setup;reference(cfg,tmp_path)
    cfg['unload'].update(low_exposure_enabled=False,vision_method='rgb_roi')
    with pytest.raises(C.ConfigError,match='normal camera'):basket.BasketInspector(cfg)


def test_low_template_has_no_clahe_or_canny_and_detects_small_changes(setup,tmp_path,monkeypatch):
    cfg,*_=setup;reference(cfg,tmp_path);monkeypatch.setattr(cv2,'Canny',Mock(side_effect=AssertionError('No edges in low template mode')))
    monkeypatch.setattr(cv2,'createCLAHE',Mock(side_effect=AssertionError('No CLAHE in low template mode')))
    inspector=basket.BasketInspector(cfg);frame=np.full((480,640,3),80,np.uint8);frame[100:120,100:120]=100
    for i in range(5):result=inspector.update(frame,1+i*.11,1+i*.11)
    assert result=='OCCUPIED'


def test_low_template_rejects_black_reference_instead_of_claiming_empty(setup,tmp_path):
    cfg,*_=setup;reference(cfg,tmp_path);inspector=basket.BasketInspector(cfg)
    result=inspector.update(np.zeros((480,640,3),np.uint8),1,1)
    assert result=='UNKNOWN' and 'ROI_UNDEREXPOSED' in inspector.snapshot(1)['quality_flags']


def test_probe_does_not_write_controls_or_connect_motors(setup,monkeypatch):
    cfg,clock,controls,camera=setup;monkeypatch.setattr(commissioning,'camera_for',lambda cfg:camera)
    result=commissioning.camera_operation(cfg,'basket-exposure')
    assert result['current']==controls.initial and not controls.writes
    camera.disconnect.assert_called_once()


def test_calibration_mode_keeps_low_inspection_disabled_until_workstation(setup):
    from omx_manager.runtime import build
    cfg,*_=setup;argv,_,env,_=build(cfg,'cfg.yaml',calibration_item='F')
    assert '--robot.cameras='+json.dumps(cfg['hardware']['cameras']) in argv
    assert all('inspection_exposure' not in arg for arg in argv)


def test_reference_controls_are_frozen_but_each_cycles_original_controls_are_restored(setup,tmp_path):
    cfg,clock,controls,camera=setup;reference(cfg,tmp_path);inspector=basket.BasketInspector(cfg)
    controls.values.update(gain=30.,white_balance=5000.);original=controls.values.copy()
    with E.Session(camera,cfg,target=inspector.exposure_profile):
        assert controls.values['gain']==20. and controls.values['white_balance']==4600.
    assert controls.values==original


def test_journal_is_written_before_the_first_sensor_write(setup):
    cfg,clock,controls,camera=setup;old_set=controls.set
    def check(key,value):
        record=json.loads(E.journal_path(cfg).read_text())
        assert record['original']==controls.initial
        old_set(key,value)
    controls.set=check
    with E.Session(camera,cfg):pass
    assert not E.journal_path(cfg).exists()


def test_restore_frame_mismatch_preserves_pending_state(setup):
    cfg,clock,controls,camera=setup;old_read=camera.read_latest_with_timestamp;changed=False
    def read(**kw):
        frame,stamp=old_read(**kw)
        if changed and controls.values['auto_exposure']==3:frame[:]=100
        return frame,stamp
    camera.read_latest_with_timestamp=read
    with pytest.raises(RuntimeError,match='restoration unverified'):
        with E.Session(camera,cfg):changed=True
    assert controls.values==controls.initial and E.journal_path(cfg).exists()


def test_interactive_watch_can_restore_after_cargo_scene_changed(setup):
    cfg,clock,controls,camera=setup;old_read=camera.read_latest_with_timestamp;changed=False
    def read(**kw):
        frame,stamp=old_read(**kw)
        if changed and controls.values['auto_exposure']==3:frame[:]=100
        return frame,stamp
    camera.read_latest_with_timestamp=read
    with E.Session(camera,cfg,allow_scene_change=True):changed=True
    assert controls.values==controls.initial and not E.journal_path(cfg).exists()


@pytest.mark.parametrize('sigma',[1.,2.])
def test_low_template_noise_and_faint_changes_do_not_become_false_empty(setup,tmp_path,sigma):
    cfg,*_=setup;reference(cfg,tmp_path);inspector=basket.BasketInspector(cfg);rng=np.random.default_rng(31)
    for i in range(5):
        frame=np.clip(80+rng.normal(0,sigma,(480,640,3)),0,255).astype(np.uint8)
        result=inspector.update(frame,1+i*.11,1+i*.11)
    assert result=='EMPTY',inspector.snapshot(1)
    frame=np.full((480,640,3),80,np.uint8);frame[200:220,200:220]=84
    assert inspector.update(frame,2,2)!='EMPTY'


def test_low_capture_metadata_is_saved_only_after_sensor_restoration(setup,tmp_path,monkeypatch):
    from omx_manager import cli
    from contextlib import nullcontext
    cfg,clock,controls,camera=setup;camera.shape=(480,640,3)
    cfg['home']['target']={j:0. for j in C.JOINTS};path=tmp_path/'cfg.yaml';C.save(path,cfg)
    monkeypatch.setattr(commissioning,'time',NS(perf_counter=clock,monotonic=clock,sleep=clock.sleep,time=clock))
    monkeypatch.setattr(commissioning,'require_home',lambda _:None);monkeypatch.setattr(cli,'require_home',lambda _:None)
    monkeypatch.setattr(commissioning,'camera_for',lambda _:camera)
    monkeypatch.setattr(cli,'device_lock',lambda _:nullcontext())
    monkeypatch.setattr(cli,'FollowerBus',lambda _:nullcontext(NS(read=lambda:cfg['home']['target'])))
    monkeypatch.setattr(cli,'state_dir',lambda _:tmp_path/'state')
    cli.invoke(path,'basket-capture',confirm_empty=True)
    assert controls.values==controls.initial and not E.journal_path(cfg).exists()
    saved=C.load(path);inspector=basket.BasketInspector(saved)
    assert inspector.exposure_profile['controls']['exposure']==100.
    assert inspector.reference.mean()==80.


def test_policy_dispatch_guard_blocks_before_inference_during_inspection(setup):
    import ast,contextlib,os
    from test_cycle_counting import method
    cfg,clock,controls,camera=setup
    wrapper=NS(inner=NS(cameras={'top':camera},_omx_manager_snapshot=cfg),send_action=Mock())
    engine=Mock();interpolator=Mock()
    ctx=NS(policy=NS(inference=engine),data=NS(dataset_features={},ordered_action_keys=list(C.JOINTS)),
           runtime=NS(shutdown_event=NS(is_set=lambda:False)),hardware=NS(robot_wrapper=wrapper))
    fn=method('lerobot_unified/src/lerobot/rollout/strategies/core.py','send_next_action')
    ns=dict(contextlib=contextlib,os=os,Path=Path)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'actual dispatcher','exec'),ns)
    with E.Session(camera,cfg):
        with pytest.raises(RuntimeError,match='blocked'):ns['send_next_action']({}, {},ctx,interpolator)
    engine.get_action.assert_not_called();wrapper.send_action.assert_not_called()


def test_pending_restoration_blocks_supervisor_before_file_hashes_or_model_loading(setup,monkeypatch):
    from omx_manager import runtime
    cfg,*_=setup;C.write_json(E.journal_path(cfg),dict(phase='PENDING'))
    check=Mock(side_effect=AssertionError('No heavy preflight while restoration pending'))
    monkeypatch.setattr(runtime,'check_files',check)
    with pytest.raises(C.ConfigError,match='restoration pending'):runtime.Supervisor('unused.yaml').preflight(cfg)
    check.assert_not_called()


def test_corrupted_recovery_record_cannot_write_sensor_controls(setup):
    cfg,clock,controls,camera=setup
    C.write_json(E.journal_path(cfg),dict(schema_version=1,device_id=cfg['device_id'],camera='top',hardware=cfg['hardware']['cameras']['top'],
                                      backend='opencv-v4l2',original={'exposure':100}))
    with pytest.raises(C.ConfigError,match='snapshot'):E.recover(camera,cfg)
    assert not controls.writes


def test_old_low_frames_after_control_restore_are_not_accepted_as_model_frames(setup):
    cfg,clock,controls,camera=setup;read=camera.read_latest_with_timestamp;remaining=10;last=None;actual=[]
    def buffered(**kw):
        nonlocal remaining,last
        frame,stamp=read(**kw)
        returning=any(key=='exposure' and value==100 for key,value in controls.writes) and controls.values['auto_exposure']==3
        if returning and remaining>0:
            frame[:]=80
            if stamp!=last:remaining-=1;last=stamp
        actual.append(int(frame.mean()));return frame,stamp
    camera.read_latest_with_timestamp=buffered
    with E.Session(camera,cfg) as session:pass
    assert remaining==0 and actual[-1]==180 and session.timing['exposure_restore_s']>.5


@pytest.mark.parametrize('device',['omx3','omx4'])
def test_two_consecutive_jobs_restore_camera_before_each_model_cycle(setup,tmp_path,monkeypatch,device):
    from test_cycle_counting import trained
    import test_workflow_recovery as F
    cfg,_,controls,_=setup;role=trained(device,wrist=0);role['unload']=cfg['unload'];cfg=role
    reference(cfg,tmp_path);cfg['unload']['reference_binding']=registration_binding(cfg)
    # Controller uses 100-grey synthetic camera frames for its two-item basket.
    spec=basket.options(cfg);path=C.path_value(cfg['unload']['reference'])
    with np.load(path) as data:profile=str(data['exposure_profile'].item())
    np.savez_compressed(path,image=np.full((240,320,3),100,np.uint8),roi=spec['roi'],roi_signature=signature(spec),exposure_profile=profile)
    run,phases,sent,done,_=F.controller(cfg,tmp_path,monkeypatch,paired=True,jobs=2,controls=controls)
    clock=F.W.time.perf_counter;monkeypatch.setattr(E,'time',NS(perf_counter=clock,sleep=clock.sleep,time=lambda:1000+clock()))
    run()
    assert len(done)==2 and phases.count('RUNNING')==4 and sent
    assert controls.values==controls.initial and not E.journal_path(cfg).exists()
