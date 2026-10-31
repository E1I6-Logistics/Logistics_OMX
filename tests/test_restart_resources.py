"""SDK-free fault injection of real camera/launcher cleanup entry points."""
import io
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest
from omx_manager import hardware as H, logs as L, runtime as R
from omx_manager import rollout_lifecycle as lifecycle
from test_runtime_reliability import load_function
from test_basket_registration import spec, scene
from omx_manager import basket_registration as B


class Reader:
    def __init__(self): self.alive=True; self.joins=0
    def is_alive(self): return self.alive
    def join(self,timeout): self.joins+=1


def camera(kind):
    return NS(thread=Reader(), stop_event=threading.Event(), frame_lock=threading.Lock(),
              new_frame_event=threading.Event(),latest_frame=object(),latest_color_frame=object(),
              latest_depth_frame=object(),latest_timestamp=123., videocapture=None,rs_pipeline=None,rs_profile=None)


def method(kind,name,**ns):
    cls='OpenCVCamera' if kind=='opencv' else 'RealSenseCamera'
    return load_function(f'lerobot_unified/src/lerobot/cameras/{kind}/camera_{kind}.py',name,cls,
                         dict(logger=Mock(),**ns))


@pytest.mark.parametrize('kind',['opencv','realsense'])
def test_blocked_reader_stays_owned_and_reconnect_cannot_open_new_device(kind):
    c=camera(kind);reader=c.thread;event=c.stop_event
    stop=method(kind,'_stop_read_thread');stop(c)
    assert c.thread is reader and c.stop_event is event and event.is_set()
    assert c.latest_timestamp is None and not c.new_frame_event.is_set()
    cv=Mock();c._open_pipeline=Mock()
    with pytest.raises(RuntimeError,match='previous camera reader'):
        method(kind,'connect',cv2=cv)(c)
    cv.VideoCapture.assert_not_called();c._open_pipeline.assert_not_called()
    reader.alive=False;stop(c)
    assert c.thread is None and c.stop_event is None


@pytest.mark.parametrize('kind',['opencv','realsense'])
def test_native_release_unblocks_reader_before_reconnect(kind):
    c=camera(kind);reader=c.thread
    c._stop_read_thread=lambda:method(kind,'_stop_read_thread')(c)
    native=NS(release=lambda:setattr(reader,'alive',False),stop=lambda:setattr(reader,'alive',False))
    c.videocapture=c.rs_pipeline=native
    method(kind,'_cleanup_resources')(c)
    assert not reader.alive and c.thread is None and c.stop_event is None


@pytest.mark.parametrize('kind',['opencv','realsense'])
def test_failed_native_release_still_quarantines_blocked_reader(kind):
    c=camera(kind);reader=c.thread;event=c.stop_event
    c._stop_read_thread=lambda:method(kind,'_stop_read_thread')(c)
    c.videocapture=c.rs_pipeline=NS(release=Mock(),stop=Mock())
    with pytest.raises(RuntimeError,match='previous camera reader'):
        method(kind,'_cleanup_resources')(c)
    assert c.thread is reader and c.stop_event is event and event.is_set()


@pytest.mark.parametrize('kind',['opencv','realsense'])
def test_late_native_frame_cannot_repopulate_stopped_buffer(kind):
    c=camera(kind);c.processing_fps=None;c.use_rgb=True;c.use_depth=False
    c.latest_frame=c.latest_color_frame=c.latest_depth_frame=c.latest_timestamp=None
    image=np.zeros((8,8,3),np.uint8)
    def read():
        c.stop_event.set()
        return image if kind=='opencv' else NS(get_color_frame=lambda:NS(get_data=lambda:image))
    c._read_from_hardware=read;c._postprocess_image=lambda x:x
    method(kind,'_read_loop',time=time,np=np,camera_event=Mock(),DeviceNotConnectedError=ConnectionError)(c)
    assert c.latest_timestamp is None and c.latest_frame is None and c.latest_color_frame is None
    assert not c.new_frame_event.is_set()


@pytest.mark.parametrize('phase',['construct','setup','teardown'])
def test_rollout_errors_always_release_hardware_and_keep_original_error(phase,monkeypatch):
    cleanup=Mock(return_value=[]);monkeypatch.setattr(H,'cleanup_resources',cleanup)
    ctx=NS(hardware=NS(robot_wrapper=NS(inner=object()),teleop=NS(disconnect=Mock())),policy=NS(inference=object()))
    strategy=None if phase=='construct' else NS(teardown=Mock(side_effect=RuntimeError('teardown failed')))
    lifecycle.finish(ctx,strategy,original_error=True)
    cleanup.assert_called_once_with(ctx.policy.inference,ctx.hardware.robot_wrapper.inner)
    ctx.hardware.teleop.disconnect.assert_called_once()
    if phase=='teardown':
        with pytest.raises(RuntimeError,match='teardown failed'):
            lifecycle.finish(ctx,strategy)


@pytest.mark.parametrize('phase',['build','construct','setup','run'])
def test_actual_launcher_protects_context_strategy_and_visualization(phase):
    cfg=NS(display_data=True,display_mode='local',display_ip=None,display_port=None,interactive=False,
           robot=NS(type='omx_follower'),fps=30,duration=0,strategy=NS(type='base'))
    event=threading.Event();event.set();ctx=NS();finish=Mock();visual=Mock()
    strategy=NS(setup=Mock(),run=Mock())
    build=Mock(return_value=ctx);create=Mock(return_value=strategy)
    {'build':build,'construct':create,'setup':strategy.setup,'run':strategy.run}[phase].side_effect=RuntimeError('injected failure')
    # Exercise the actual launcher function without importing Torch or SDKs.
    import omx_manager.rollout_lifecycle as module
    original=module.finish;module.finish=finish
    try:
        launch=load_function('lerobot_unified/src/lerobot/scripts/lerobot_rollout.py','rollout',namespace=dict(
            RolloutConfig=object,init_logging=Mock(),logger=Mock(),init_visualization=Mock(),
            ProcessSignalHandler=lambda **_:NS(shutdown_event=event),build_rollout_context=build,
            create_strategy=create,shutdown_visualization=visual))
        with pytest.raises(RuntimeError,match='injected failure'):launch(cfg)
        finish.assert_called_once_with(None if phase=='build' else ctx,
                                       strategy if phase in ('setup','run') else None,original_error=True)
        visual.assert_called_once()
    finally:module.finish=original


def test_failed_child_reap_blocks_preflight_and_new_launch(monkeypatch):
    s=R.Supervisor('unused');proc=NS(pid=123)
    s.pending_cleanup=[proc]
    monkeypatch.setattr(R,'stop_process',Mock(side_effect=TimeoutError('still alive')))
    cfg={'runtime':{'shutdown_timeout_s':.1}}
    for entry in (s.preflight,s.run_once):
        with pytest.raises(RuntimeError,match='new hardware session blocked'):entry(cfg)
    assert s.pending_cleanup==[proc]
    monkeypatch.setattr(R,'stop_process',Mock());s.reap_pending(cfg)
    assert s.pending_cleanup==[]


def test_realsense_warmup_retry_cannot_replace_blocked_pipeline():
    c=camera('realsense');sdk=Mock()
    with pytest.raises(RuntimeError,match='previous camera reader'):
        method('realsense','_open_pipeline',rs=sdk)(c)
    sdk.pipeline.assert_not_called()


def test_log_readers_do_not_accumulate_across_restarts(tmp_path):
    c=L.Capture(tmp_path,important_only=True)
    try:
        for _ in range(30):
            c.attach(NS(stdout=io.StringIO('')),'rollout')
            c.readers[-1].join(timeout=1)
        assert len(c.readers)==1
    finally:c.close()


@pytest.mark.parametrize('change,label',[
    ({'registration_max_shift_px':10},'SHIFT_LIMIT'),
    ({'registration_max_rotation_deg':1},'ROTATION_LIMIT')])
def test_rejected_pose_explains_geometry_without_relaxing_limits(change,label):
    s=spec();image,_=scene();tracker=B.Tracker(s);tracker.extract(image)
    s.update(change);current,_=scene((35,0),8)
    with pytest.raises(B.ViewError) as error:B.Tracker(s,tracker.last_profile).extract(current)
    assert label in error.value.details['rejections']
    assert error.value.details['nearest_rejected']['reason']==label
    assert error.value.details['search_bounds']


def test_failed_inspection_persists_pose_rejection_details(tmp_path):
    from test_basket_registration import registered_config
    from omx_manager.basket import BasketInspector
    inspector=BasketInspector(registered_config(tmp_path))
    inspector.spec['registration_max_shift_px']=10
    frame,_=scene((35,0),8)
    assert inspector.update(frame,1.,1.)=='UNKNOWN'
    row=inspector.snapshot(1.)
    assert row['registration_detail']['nearest_rejected']['reason']=='SHIFT_LIMIT'
    assert row['registration']=='REJECTED'
