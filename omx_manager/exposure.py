"""Inspection-only sensor transaction, with durable recovery and policy fencing."""
from contextlib import contextmanager
import json
import logging
import math
from pathlib import Path
import time

from .config import ConfigError,ROOT,write_json

logger=logging.getLogger(__name__)
DEFAULTS=dict(low_exposure_enabled=False,inspection_exposure=None,
              exposure_discard_frames=5,exposure_settle_s=.2,
              exposure_switch_timeout_s=5.,exposure_restore_luma_tolerance=8.,
              inspection_min_luma=15.,inspection_max_dark_ratio=.3)


def validate(spec):
    from .config import number
    if type(spec['low_exposure_enabled']) is not bool:raise ConfigError('low_exposure_enabled must be boolean')
    if spec['inspection_exposure'] is not None:number(spec['inspection_exposure'],'inspection_exposure',1,1000000)
    if spec['low_exposure_enabled'] and spec['inspection_exposure'] is None:
        raise ConfigError('Set a camera-specific inspection exposure first: omx_basket exposure / low VALUE')
    if type(spec['exposure_discard_frames']) is not int or not 2<=spec['exposure_discard_frames']<=30:
        raise ConfigError('exposure_discard_frames must be 2..30')
    for key,lo,hi in (('exposure_settle_s',.1,2),('exposure_switch_timeout_s',2,20),('exposure_restore_luma_tolerance',1,20),
                      ('inspection_min_luma',5,60),('inspection_max_dark_ratio',.05,.5)):
        number(spec[key],key,lo,hi)
    if spec['exposure_settle_s']>=spec['exposure_switch_timeout_s']:raise ConfigError('Exposure settle exceeds timeout')


def journal_path(cfg):return ROOT/'state'/'manager'/cfg['device_id']/'camera_exposure_pending.json'


def require_restored(cfg):
    if journal_path(cfg).exists():raise ConfigError('Camera exposure restoration pending; stop manager, use omx_basket restore, inspect cargo, then restart')


def ensure_policy_ready(robot):
    cameras=getattr(robot,'cameras',{})
    if isinstance(cameras,dict) and any(getattr(c,'_omx_exposure_blocked',False) is True for c in cameras.values()):
        raise RuntimeError('Camera inspection/restoration active; policy motor actions blocked')
    cfg=getattr(robot,'_omx_manager_snapshot',None)
    if isinstance(cfg,dict):require_restored(cfg)


def finite(value):
    value=float(value)
    if not math.isfinite(value) or value<0:raise ConfigError('Camera returned an unsupported/nonfinite control value')
    return value


def validate_profile(profile,spec):
    if not isinstance(profile,dict) or set(profile)!={'backend','controls'}:
        raise ConfigError('Register a new low-exposure empty reference: omx_basket capture')
    if profile['backend'] not in ('opencv-v4l2','realsense-rgb'):raise ConfigError('Unknown reference exposure backend')
    row=profile['controls'];required={'auto_exposure','exposure','gain'};allowed=required|{'auto_white_balance','white_balance'}
    if not isinstance(row,dict) or not required<=set(row)<=allowed:
        raise ConfigError('Invalid reference exposure controls')
    if ('white_balance' in row)!=('auto_white_balance' in row):raise ConfigError('Incomplete reference white balance controls')
    for value in row.values():
        if type(value) not in (int,float):raise ConfigError('Reference camera controls must be numeric')
        finite(value)
    manual=1. if profile['backend']=='opencv-v4l2' else 0.
    if row['auto_exposure']!=manual or row.get('auto_white_balance',0.)!=0.:
        raise ConfigError('Inspection reference must use fixed camera controls')
    if abs(row['exposure']-spec['inspection_exposure'])>.51:raise ConfigError('Inspection exposure changed; recapture empty reference')


def validate_original(row,adapter):
    required={'auto_exposure','exposure','gain'};allowed=required|{'auto_white_balance','white_balance'}
    if not isinstance(row,dict) or not required<=set(row)<=allowed:raise ConfigError('Invalid original camera control snapshot')
    for value in row.values():
        if type(value) not in (int,float):raise ConfigError('Camera controls must be numeric')
        finite(value)
    if row['exposure']<1 or row['auto_exposure'] not in ((1.,3.) if adapter.backend=='opencv-v4l2' else (0.,1.)):
        raise ConfigError('Invalid original camera exposure mode/value')
    if ('white_balance' in row)!=('auto_white_balance' in row) or row.get('auto_white_balance',0.) not in (0.,1.) or row.get('white_balance',1.)<=0:
        raise ConfigError('Invalid original white balance controls')


class OpenCVControls:
    """Raw V4L2 controls only; normalized/Windows units are explicitly rejected."""
    def __init__(self,camera):
        import cv2
        self.camera=camera;self.cap=camera.videocapture
        if self.cap is None or self.cap.getBackendName()!='V4L2':raise ConfigError('Low exposure requires OpenCV V4L2 backend')
        self.lock=camera.control_lock
        self.props=dict(auto_exposure=cv2.CAP_PROP_AUTO_EXPOSURE,exposure=cv2.CAP_PROP_EXPOSURE,gain=cv2.CAP_PROP_GAIN,
                        auto_white_balance=cv2.CAP_PROP_AUTO_WB,white_balance=cv2.CAP_PROP_WB_TEMPERATURE)
        self.backend='opencv-v4l2';self.manual=1.
        with self.access():
            if self.cap.get(cv2.CAP_PROP_MODE)!=0:raise ConfigError('Normalized V4L2 controls unsupported; use raw units')
    @contextmanager
    def access(self):
        if not self.lock.acquire(timeout=1.):raise TimeoutError('Camera capture/control lock busy')
        try:yield
        finally:self.lock.release()
    def snapshot(self):
        with self.access():
            result={key:finite(self.cap.get(self.props[key])) for key in ('auto_exposure','exposure','gain')}
            if result['auto_exposure'] not in (1.,3.) or result['exposure']<1:
                raise ConfigError('Unsupported V4L2 exposure mode/value; no controls changed')
            awb=self.cap.get(self.props['auto_white_balance']);wb=self.cap.get(self.props['white_balance'])
            if awb in (0.,1.) and math.isfinite(wb) and wb>0:
                result.update(auto_white_balance=float(awb),white_balance=float(wb))
            return result
    def get(self,key):
        with self.access():return finite(self.cap.get(self.props[key]))
    def set(self,key,value):
        with self.access():
            if not self.cap.set(self.props[key],float(value)):raise ConfigError('Camera rejected control '+key)
            readback=finite(self.cap.get(self.props[key]))
        if abs(readback-value)>.51:raise ConfigError(f'Camera control {key} clamped/ignored: requested={value}, actual={readback}')
    def ranges(self):return None


class RealSenseControls:
    def __init__(self,camera):
        import pyrealsense2 as rs
        self.sensor=camera._get_color_sensor();self.backend='realsense-rgb';self.manual=0.
        self.props=dict(auto_exposure=rs.option.enable_auto_exposure,exposure=rs.option.exposure,gain=rs.option.gain,
                        auto_white_balance=rs.option.enable_auto_white_balance,white_balance=rs.option.white_balance)
        for key in ('auto_exposure','exposure','gain'):
            if not self.sensor.supports(self.props[key]):raise ConfigError('RGB sensor lacks '+key)
    def get(self,key):return finite(self.sensor.get_option(self.props[key]))
    def snapshot(self):
        result={key:self.get(key) for key in ('auto_exposure','exposure','gain')}
        if result['auto_exposure'] not in (0.,1.) or result['exposure']<1:raise ConfigError('Unsupported RGB exposure mode/value')
        if all(self.sensor.supports(self.props[k]) for k in ('auto_white_balance','white_balance')):
            result.update(auto_white_balance=self.get('auto_white_balance'),white_balance=self.get('white_balance'))
        return result
    def set(self,key,value):
        self.sensor.set_option(self.props[key],float(value))
        if abs(self.get(key)-value)>.51:raise ConfigError('RGB sensor clamped/ignored '+key)
    def ranges(self):
        r=self.sensor.get_option_range(self.props['exposure'])
        return dict(min=r.min,max=r.max,step=r.step)


def controls_for(camera):
    custom=getattr(camera,'_omx_test_controls',None)
    if custom is not None:return custom # fake devices used by hardware-free tests
    if hasattr(camera,'rs_profile'):return RealSenseControls(camera)
    if hasattr(camera,'videocapture'):return OpenCVControls(camera)
    raise ConfigError('Camera does not support transactional exposure control')


def apply(adapter,values):
    adapter.set('auto_exposure',adapter.manual)
    if 'auto_white_balance' in values:adapter.set('auto_white_balance',0.)
    for key in ('exposure','gain','white_balance'):
        if key in values:adapter.set(key,values[key])


def restore_controls(adapter,values):
    # Attempt every control even after one fails; never leave auto mode disabled
    # merely because another register rejected a write.
    failures=[]
    def attempt(key,value):
        try:
            if abs(adapter.get(key)-value)>.51:adapter.set(key,value)
        except BaseException as exc:failures.append(f'{key}: {exc}')
    attempt('auto_exposure',adapter.manual)
    if 'auto_white_balance' in values:attempt('auto_white_balance',0.)
    for key in ('exposure','gain','white_balance'):
        if key in values:attempt(key,values[key])
    if 'auto_white_balance' in values:attempt('auto_white_balance',values['auto_white_balance'])
    attempt('auto_exposure',values['auto_exposure'])
    # Auto loops may immediately adjust exposure/gain/WB; only their enabled
    # flags are invariant then. Manual values must read back exactly.
    for key in values:
        if key in ('exposure','gain') and values['auto_exposure']!=adapter.manual:continue
        if key=='white_balance' and values.get('auto_white_balance')==1.:continue
        try:
            if abs(adapter.get(key)-values[key])>.51:failures.append('Readback mismatch: '+key)
        except BaseException as exc:failures.append(str(exc))
    if failures:raise RuntimeError('Exposure restoration failed: '+'; '.join(failures))


def luma(frame):
    import cv2
    return float(cv2.cvtColor(cv2.resize(frame,(32,24)),cv2.COLOR_RGB2GRAY).mean())


class Session:
    def __init__(self,camera,cfg,*,target=None,guard=lambda:None,stopped=lambda:False,clock=None,sleep=None,allow_scene_change=False):
        from .basket import options
        self.camera=camera;self.cfg=cfg;self.spec=options(cfg);self.adapter=controls_for(camera)
        self.target=target;self.guard=guard;self.stopped=stopped;self.clock=clock or time.perf_counter;self.sleep=sleep or time.sleep
        self.path=journal_path(cfg);self.original=None;self.started=None;self.timing={}
        self.allow_scene_change=allow_scene_change
    def wait_frames(self,after,*,restoring=False,baseline=None):
        from .basket import read_camera
        from .hooks import heartbeat
        deadline=self.clock()+self.spec['exposure_switch_timeout_s'];last=None;seen=0;held=None;previous=None
        while self.clock()<deadline:
            if not restoring:
                if self.stopped():raise InterruptedError('Inspection stopped')
            heartbeat()
            try:frame,stamp=read_camera(self.camera,self.spec['max_frame_age_s'])
            except TimeoutError:self.sleep(.01);continue
            if stamp<=after or (last is not None and stamp<=last):self.sleep(.01);continue
            if not restoring:self.guard()
            gap=last is not None and stamp-last>self.cfg['home']['max_sample_gap_s']
            last=stamp;seen+=1;value=luma(frame)
            if seen<=self.spec['exposure_discard_frames']:previous=value;continue
            stable=previous is not None and abs(value-previous)<=self.spec['stability_delta'] and not gap
            match=baseline is None or abs(value-baseline)<=self.spec['exposure_restore_luma_tolerance']
            if stable and match:held=stamp if held is None else held
            else:held=None
            previous=value
            if held is not None and stamp-held>=self.spec['exposure_settle_s']:return frame,stamp
            self.sleep(.01)
        raise TimeoutError('Original camera frames did not recover/stabilize' if restoring else 'Inspection camera frames did not stabilize')
    def __enter__(self):
        require_restored(self.cfg)
        if self.stopped():raise InterruptedError('Inspection stopped before sensor changes')
        self.guard();self.original=self.adapter.snapshot();validate_original(self.original,self.adapter)
        # Pre-change fresh frame provides an original-scene brightness check.
        baseline_frame,_=self.wait_frames(self.clock())
        self.baseline=luma(baseline_frame)
        if self.target is not None:
            validate_profile(self.target,self.spec)
            if self.target['backend']!=self.adapter.backend:raise ConfigError('Reference sensor backend changed; recapture empty reference')
            if set(self.target['controls'])!=set(self.original):raise ConfigError('Sensor control capabilities changed')
            self.target=dict(self.target['controls'])
        else:self.target=dict(self.original)
        self.target['exposure']=float(self.spec['inspection_exposure'])
        self.target['auto_exposure']=self.adapter.manual
        if 'auto_white_balance' in self.target:self.target['auto_white_balance']=0.
        self.started=self.clock()
        self.record=dict(schema_version=1,device_id=self.cfg['device_id'],camera=self.spec['camera'],
                         hardware=self.cfg['hardware']['cameras'][self.spec['camera']],backend=self.adapter.backend,
                         original=self.original,baseline_luma=self.baseline,phase='PENDING',at=time.time())
        write_json(self.path,self.record)
        self.camera._omx_exposure_blocked=True
        try:
            apply(self.adapter,self.target)
            self.wait_frames(self.clock())
            actual={key:self.adapter.get(key) for key in self.target}
            mismatches=[f'{key} requested={value:g} actual={actual[key]:g}'
                        for key,value in self.target.items() if abs(actual[key]-value)>.51]
            if mismatches:
                controls=', '.join(f'{key}={value:g}' for key,value in actual.items())
                raise ConfigError('Inspection setting changed: '+'; '.join(mismatches)
                                  +f'; backend={self.adapter.backend}; controls: '+controls)
            self.timing['exposure_enter_s']=self.clock()-self.started
            logger.info('[EXPOSURE] inspection ready: %s exposure=%s',self.adapter.backend,self.target['exposure'])
            return self
        except BaseException:
            self.finish();raise
    def finish(self):
        start=self.clock()
        try:
            restore_controls(self.adapter,self.original)
            self.wait_frames(self.clock(),restoring=True,baseline=None if self.allow_scene_change else self.baseline)
            self.record.update(phase='RESTORED',at=time.time())
            write_json(self.path.with_name('camera_exposure_last.json'),self.record,durable=False)
            self.path.unlink()
            self.camera._omx_exposure_blocked=False
            self.timing['exposure_restore_s']=self.clock()-start
            self.timing['exposure_total_s']=self.clock()-self.started
            logger.info('[EXPOSURE] original controls and fresh frames restored')
        except BaseException as exc:
            self.camera._omx_exposure_blocked=True
            self.record.update(phase='RESTORE_REQUIRED',reason=str(exc),at=time.time())
            write_json(self.path,self.record)
            raise RuntimeError('Camera restoration unverified; model remains blocked; use omx_basket restore') from exc
    def __exit__(self,*_):self.finish()


def recover(camera,cfg,*,clock=None,sleep=None):
    """Explicit camera-only recovery. Never reconciles or resumes robot jobs."""
    from .basket import options
    path=journal_path(cfg)
    if not path.exists():return dict(phase='NO_PENDING_RESTORE')
    record=json.loads(path.read_text());spec=options(cfg)
    if (record.get('schema_version')!=1 or record.get('device_id')!=cfg['device_id'] or record.get('camera')!=spec['camera']
        or record.get('hardware')!=cfg['hardware']['cameras'][spec['camera']]):
        raise ConfigError('Exposure journal camera identity changed; refusing another sensor')
    session=Session(camera,cfg,clock=clock,sleep=sleep)
    if session.adapter.backend!=record['backend']:raise ConfigError('Exposure journal backend changed')
    validate_original(record.get('original'),session.adapter)
    session.original=record['original'];session.record=record;session.started=session.clock()
    # Explicit recovery can follow operator changes to cargo. Verify controls
    # plus fresh stable frames, without requiring the previous scene brightness.
    session.baseline=None;camera._omx_exposure_blocked=True
    session.finish()
    return dict(phase='RESTORED',**session.timing)


def inspect(camera,cfg,inspector,*,guard=lambda:None,stopped=lambda:False,report=lambda row:None):
    """One bounded low-exposure inspection; restore BEFORE returning verdict."""
    from .basket import read_camera
    from .hooks import heartbeat
    start=time.perf_counter();inspector.reset()
    with Session(camera,cfg,target=inspector.exposure_profile,guard=guard,stopped=stopped) as session:
        deadline=time.perf_counter()+inspector.spec['inspect_timeout_s']
        while time.perf_counter()<deadline:
            if stopped():raise InterruptedError('Inspection stopped')
            guard();heartbeat()
            frame,stamp=read_camera(camera,inspector.spec['max_frame_age_s'])
            result=inspector.update(frame,time.perf_counter(),stamp)
            row=inspector.snapshot(time.time());row.update(camera_state='INSPECTION_LOW',restore_verified=False)
            report(row)
            if result!='UNKNOWN':break
            time.sleep(inspector.spec['sample_interval_s'])
        else:raise TimeoutError('Low-exposure basket inspection remained UNKNOWN; no model action permitted')
    row=inspector.snapshot(time.time());row.update(session.timing,inspection_total_s=time.perf_counter()-start,camera_state='NORMAL_RESTORED',restore_verified=True)
    row['frame_age_ms']=(time.perf_counter()-inspector.last_frame)*1000
    report(row)
    return result
