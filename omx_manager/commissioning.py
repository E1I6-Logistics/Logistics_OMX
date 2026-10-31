"""Follower-only registration. Captures never move or recalibrate motors."""
import copy
import time

from .config import ConfigError, profile, require_home
from .home import errors, stable_pose
from .workflow import registration_binding, require_stages


class CaptureViewError(ConfigError):
    def __init__(self,reason,frame,stamp):
        super().__init__('Empty reference camera not stable/fresh; '+reason)
        self.reason=reason;self.frame=frame;self.stamp=stamp


def initialize(cfg):
    from .basket import DEFAULTS
    result=copy.deepcopy(cfg)
    result.setdefault('workflow',{'items':{}})
    from .cycles import DEFAULTS as CYCLE_DEFAULTS
    for key,value in CYCLE_DEFAULTS.items():result['counting'].setdefault(key,copy.deepcopy(value))
    if cfg['role']=='unload':
        unload=result.setdefault('unload',{})
        for key,value in DEFAULTS.items():unload.setdefault(key,copy.deepcopy(value))
    for key,value in dict(auto_home=True,poll_s=5.,routes={},interrupted_order_action='fail_and_idle').items(): result['recovery'].setdefault(key,value)
    for key,value in dict(camera_auto_reconnect=True,camera_reconnect_attempts=2,camera_reconnect_interval_s=1.).items():
        result['runtime'].setdefault(key,value)
    return result


def tune(cfg):
    result=initialize(cfg)
    result['runtime']['cpu_threads']=4
    result['counting']['mode']='cycle'
    if cfg['role']=='unload':
        result['unload'].update(sample_interval_s=.1,stable_samples=3,confirmation_s=.2,max_frame_age_s=.25)
        from .rgb_roi import DEFAULTS as RGB_DEFAULTS
        for key,value in RGB_DEFAULTS.items():result['unload'].setdefault(key,copy.deepcopy(value))
        from .exposure import DEFAULTS as EXPOSURE_DEFAULTS
        for key,value in EXPOSURE_DEFAULTS.items():result['unload'].setdefault(key,copy.deepcopy(value))
        result['unload']['vision_method']='low_template' if result['unload']['low_exposure_enabled'] else 'rgb_roi'
    return result


def require_commissioned(cfg):
    require_home(cfg)
    missing=[]
    for item in profile(cfg)['items']:
        try:
            from .cycles import options,require_profile
            if options(cfg)['mode']=='cycle':require_profile(cfg,item)
            else:require_stages(cfg,item)
        except ConfigError as exc: missing.append(str(exc))
    if cfg['role']=='unload':
        from .basket import BasketInspector
        try:
            inspector=BasketInspector(cfg)
            if inspector.spec['registration_allow_partial'] and not inspector.spec['registration_visible_confirmed']:
                raise ConfigError('Confirm visible-only inspection with omx_basket confirm-visible')
            if inspector.comparator and not inspector.spec['registration_allow_partial'] and not inspector.spec['full_view_confirmed']:
                raise ConfigError('Confirm entire basket interior and parking variation coverage before automatic unloading: unload.full_view_confirmed')
            if inspector.comparator and inspector.comparator.reference_clipped>inspector.spec['max_clipped_ratio']:
                raise ConfigError('Empty RGB ROI reference is overexposed; automatic empty verification unavailable. Inspect using omx_basket watch')
            if inspector.spec['low_exposure_enabled'] and (inspector.comparator.reference_dark>inspector.spec['inspection_max_dark_ratio']
                    or float(inspector.comparator.reference_mean.mean())<inspector.spec['inspection_min_luma']):
                raise ConfigError('Low-exposure reference is too dark; test another exposure with low-snapshot and recapture')
        except (ConfigError,OSError,ValueError) as exc: missing.append(str(exc))
    if missing: raise ConfigError(' / '.join(missing))


def sample_pose(device,cfg):
    samples=[]
    end=time.monotonic()+cfg['home']['capture_s']
    while time.monotonic()<end:
        samples.append(device.read());time.sleep(.05)
    return stable_pose(samples,cfg['home']['capture_max_span'])


def capture_stage(cfg,device,item,name):
    if item not in profile(cfg)['items'] or not isinstance(name,str) or not name.strip():
        raise ConfigError('Specify this device item and --name pick/place etc.')
    require_home(cfg)
    target=sample_pose(device,cfg)
    cfg=initialize(cfg)
    row=cfg['workflow']['items'].setdefault(item,{'binding':registration_binding(cfg),'stages':[]})
    if row.get('binding')!=registration_binding(cfg):
        raise ConfigError('Stage registration belongs to another Home; remove/re-register stages explicitly')
    stage=dict(name=name,target=target,tolerance=5.,samples=2)
    existing=next((i for i,s in enumerate(row['stages']) if s['name']==name),None)
    if existing is None: row['stages'].append(stage)
    else: row['stages'][existing]=stage
    return cfg


def capture_route(cfg,device,name):
    if not isinstance(name,str) or not name.strip(): raise ConfigError('Specify --name for the route')
    require_home(cfg)
    target=sample_pose(device,cfg)
    cfg=initialize(cfg)
    route=cfg['recovery']['routes'].setdefault(name,dict(points=[],radius=1.,approved=None))
    if max(errors(target,cfg['home']['target']).values())<=cfg['home']['arrival_tolerance']:
        target=cfg['home']['target'].copy()
    if route['points'] and target==route['points'][-1]: raise ConfigError('Duplicate waypoint')
    route['points'].append(target);route['approved']=None
    return cfg


def camera_for(cfg):
    import draccus
    from lerobot.cameras import CameraConfig,make_cameras_from_configs
    from lerobot.cameras.opencv import OpenCVCameraConfig  # register choices
    from lerobot.cameras.realsense import RealSenseCameraConfig
    from .basket import options
    name=options(cfg)['camera']
    spec=draccus.decode(CameraConfig,cfg['hardware']['cameras'][name])
    return make_cameras_from_configs({name:spec})[name]


def basket_operation(cfg,device,*,capture=False,watch=False,samples_limit=0,diagnose=False,json_output=False):
    from .basket import options,BasketInspector
    from .exposure import Session,require_restored
    require_home(cfg)
    def check_pose():
        if max(errors(device.read(),cfg['home']['target']).values())>cfg['home']['arrival_tolerance']:
            raise ConfigError('Basket inspection/reference capture requires strict registered Home')
    check_pose();spec=options(cfg);require_restored(cfg)
    inspector=None if capture else BasketInspector(cfg)
    def finalize(result):
        if not diagnose:return result
        age=result.get('frame_age_ms')
        if (inspector is None or inspector.latest is None or not isinstance(age,(int,float))
                or not 0<=age<=spec['max_frame_age_s']*1000):
            raise ConfigError('No fresh ROI available for comparison: '+str(result.get('reason','UNKNOWN')))
        if spec['registration_enabled']:
            from .basket_registration import comparison_view
        else:from .rgb_roi import comparison_view
        if spec['registration_enabled']:
            image,details=comparison_view(inspector.reference,inspector.latest,spec,inspector.reference_valid_mask,inspector.latest_valid_mask)
        else:image,details=comparison_view(inspector.reference,inspector.latest,spec)
        return dict(result,diagnostic_image=image,diagnostic=details,
                    diagnostic_frame_at=time.time()-(time.perf_counter()-inspector.last_frame))

    if capture=='low_snapshot' and not spec['low_exposure_enabled']:
        raise ConfigError('Configure omx_basket low VALUE before low-snapshot')
    camera=camera_for(cfg);preview=None
    try:
        opened=time.perf_counter()
        print(f"[BASKET] 카메라 연결/준비 중 (설정 warmup={cfg['hardware']['cameras'][spec['camera']].get('warmup_s',0)}초).",flush=True)
        camera.connect();connection_s=time.perf_counter()-opened
        from .viewer import attach
        preview=attach(cfg,camera)
        print(f'[BASKET] 카메라 준비 완료: {connection_s:.2f}초',flush=True)
        if capture=='snapshot':
            frame=camera.read_latest().copy();check_pose();return frame
        if spec['low_exposure_enabled']:
            target=inspector.exposure_profile if inspector else None
            with Session(camera,cfg,target=target,guard=check_pose,allow_scene_change=watch) as session:
                print(f"[BASKET] 검사 전용 노출={session.target['exposure']} / 모델 동작 차단. 종료 시 원래 설정을 복원합니다.",flush=True)
                def check_inspection():
                    check_pose();session.verify()
                result=_basket_connected(cfg,camera,check_inspection,inspector,capture,watch,samples_limit,opened,connection_s,
                                         dict(inspection_exposure=session.target['exposure'],camera_state='INSPECTION_LOW'),json_output=json_output)
                profile=session.profile()
            if capture:
                if isinstance(result,dict):return dict(result,exposure_profile=profile,timings=session.timing)
                return dict(image=result,exposure_profile=profile,timings=session.timing)
            result.update(session.timing,camera_state='NORMAL_RESTORED');return finalize(result)
        return finalize(_basket_connected(cfg,camera,check_pose,inspector,capture,watch,samples_limit,opened,connection_s,{},json_output=json_output))
    except CaptureViewError as exc:
        # This catch runs AFTER Session.__exit__ restores camera controls.
        from .config import ROOT,write_json
        from .viewer import write_bytes
        import cv2
        directory=ROOT/'state'/'manager'/cfg['device_id'];directory.mkdir(parents=True,exist_ok=True)
        row=dict(operation='empty_reference_capture',reason=exc.reason,at=time.time(),existing_reference_unchanged=True,
                 restore_verified=not bool(getattr(camera,'_omx_exposure_blocked',False)))
        if exc.frame is not None:
            view=cv2.cvtColor(exc.frame,cv2.COLOR_RGB2BGR)
            cv2.putText(view,exc.reason,(12,24),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,180,255),1)
            ok,png=cv2.imencode('.png',view)
            if ok:
                destination=directory/'basket_failure.png';write_bytes(destination,png.tobytes())
                row.update(image_path=str(destination),image_at=time.time()-(time.perf_counter()-exc.stamp))
        write_json(directory/'basket_capture_failure.json',row)
        raise ConfigError(str(exc)+'; previous reference unchanged; see '+str(directory/'basket_capture_failure.json')) from exc
    finally:
        if preview:preview.close()
        if camera.is_connected:camera.disconnect()


def _basket_connected(cfg,camera,check_pose,inspector,capture,watch,samples_limit,opened,connection_s,metadata,*,json_output=False):
    import numpy as np
    from .basket import options,crop,read_camera
    from .hooks import heartbeat
    spec=options(cfg)
    if capture=='low_snapshot':
        frame,_=read_camera(camera,spec['max_frame_age_s']);check_pose();return frame.copy()
    if capture:
        tracker=None
        if spec['registration_enabled']:
            from .basket_registration import Tracker
            tracker=Tracker(spec)
        samples=[];sample_masks=[];last=None;deadline=time.perf_counter()+spec['inspect_timeout_s']
        last_reason='WAIT_STABLE_FRAMES';last_image=None;last_stamp=None;next_notice=0.
        while time.perf_counter()<deadline and len(samples)<spec['stable_samples']:
            check_pose();heartbeat()
            try:frame,stamp=read_camera(camera,spec['max_frame_age_s'])
            except TimeoutError:
                samples=[];sample_masks=[];last_reason='STALE_FRAME'
                if tracker:tracker.pose_anchor=None
                time.sleep(spec['sample_interval_s']);continue
            if last is None or stamp>last:
                last_image=frame;last_stamp=stamp
                from .basket_registration import ViewError
                try:image=tracker.extract(frame) if tracker else crop(frame,spec)
                except ViewError as exc:
                    samples=[];sample_masks=[];last=stamp;last_reason=str(exc)
                    tracker.pose_anchor=None
                    if time.perf_counter()>=next_notice:
                        print('[BASKET] 기준 등록 재탐색: '+last_reason,flush=True);next_notice=time.perf_counter()+1.
                    time.sleep(spec['sample_interval_s']);continue
                if tracker and not tracker.last_info['pose_stable']:
                    last_reason='WAIT_STABLE_POSE'
                    samples=[];sample_masks=[];last=stamp;time.sleep(spec['sample_interval_s']);continue
                if samples and float(np.abs(image.astype(float)-samples[-1]).mean())>spec['stability_delta']:
                    samples=[];sample_masks=[];last_reason='FRAME_CHANGE'
                samples.append(image);last=stamp
                if tracker:sample_masks.append(tracker.valid_mask.copy())
            time.sleep(spec['sample_interval_s'])
        if len(samples)<spec['stable_samples']:raise CaptureViewError(last_reason,last_image,last_stamp)
        result=np.median(np.stack(samples),axis=0).astype(np.uint8);check_pose()
        if tracker:return dict(image=result,registration_profile=tracker.last_profile,registration_mask=np.logical_and.reduce(sample_masks))
        return result
    deadline=time.perf_counter()+spec['inspect_timeout_s'];iterations=0
    if watch:
        from .basket_console import WatchOutput
        from .config import ROOT,write_json
        destination=ROOT/'state'/'manager'/cfg['device_id']/'basket_watch.json'
        output=WatchOutput(cfg['device_id'],json_output=json_output,clock=time.perf_counter,
                           save=lambda row:write_json(destination,row,durable=False))
        if not json_output:
            scope='보이는 영역만 검사 · 화면 밖 미확인' if spec['registration_allow_partial'] else '전체 시야 검사'
            print('[BASKET] '+scope+' / 상태 변경 즉시 · 유지 시 5초 / 상세: omx_basket watch --json',flush=True)
    while watch or time.perf_counter()<deadline:
        check_pose();heartbeat()
        try:
            frame,stamp=read_camera(camera,spec['max_frame_age_s'])
            result=inspector.update(frame,time.perf_counter(),stamp)
        except TimeoutError:result=inspector.update(None,time.perf_counter(),None)
        snapshot=inspector.snapshot(time.time())
        snapshot.update(metadata,connection_s=connection_s,elapsed_s=time.perf_counter()-opened)
        if watch:
            output.update(snapshot)
            iterations+=1
            if samples_limit and iterations>=samples_limit:return snapshot
        if result!='UNKNOWN' and not watch:return snapshot
        time.sleep(spec['sample_interval_s'])
    return inspector.snapshot(time.time())


def camera_operation(cfg,command):
    from .exposure import controls_for,recover,require_restored
    if cfg['role']!='unload':raise ConfigError('Inspection exposure is for OMX3/4')
    if command!='basket-restore-exposure':require_restored(cfg)
    camera=camera_for(cfg)
    try:
        camera.connect()
        if command=='basket-restore-exposure':return recover(camera,cfg)
        controls=controls_for(camera);current=controls.snapshot()
        suggested=max(1,int(current['exposure']/4));ranges=controls.ranges()
        if ranges:
            suggested=max(ranges['min'],min(ranges['max'],suggested))
            if ranges['step']>0:suggested=ranges['min']+round((suggested-ranges['min'])/ranges['step'])*ranges['step']
        return dict(backend=controls.backend,current=current,exposure_range=ranges,
                    suggested_test_exposure=suggested,next_test_command=f'omx_basket low {suggested:g}',
                    note='Read-only probe. Suggested value is only a test candidate; confirm low-snapshot and restoration on this camera.')
    finally:
        if camera.is_connected:camera.disconnect()
