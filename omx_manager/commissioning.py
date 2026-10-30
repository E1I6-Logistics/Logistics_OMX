"""Follower-only registration. Captures never move or recalibrate motors."""
import copy
import time

from .config import ConfigError, profile, require_home
from .home import errors, stable_pose
from .workflow import registration_binding, require_stages


def initialize(cfg):
    from .basket import DEFAULTS
    result=copy.deepcopy(cfg)
    result.setdefault('workflow',{'items':{}})
    from .cycles import DEFAULTS as CYCLE_DEFAULTS
    for key,value in CYCLE_DEFAULTS.items():result['counting'].setdefault(key,copy.deepcopy(value))
    if cfg['role']=='unload': result.setdefault('unload',dict(DEFAULTS))
    for key,value in dict(auto_home=True,poll_s=5.,routes={}).items(): result['recovery'].setdefault(key,value)
    return result


def tune(cfg):
    result=initialize(cfg)
    result['runtime']['cpu_threads']=4
    result['counting']['mode']='cycle'
    if cfg['role']=='unload':
        result['unload'].update(sample_interval_s=.1,stable_samples=3,confirmation_s=.2,max_frame_age_s=.25)
        from .rgb_roi import DEFAULTS as RGB_DEFAULTS
        for key,value in RGB_DEFAULTS.items():result['unload'].setdefault(key,copy.deepcopy(value))
        result['unload']['vision_method']='rgb_roi'
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
            if inspector.comparator and not inspector.spec['full_view_confirmed']:
                raise ConfigError('Confirm entire basket interior and parking variation coverage before automatic unloading: unload.full_view_confirmed')
            if inspector.comparator and inspector.comparator.reference_clipped>inspector.spec['max_clipped_ratio']:
                raise ConfigError('Empty RGB ROI reference is overexposed; automatic empty verification unavailable. Inspect using omx_basket watch')
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


def basket_operation(cfg,device,*,capture=False,watch=False,samples_limit=0):
    import numpy as np
    from .basket import options,crop,BasketInspector,read_camera
    require_home(cfg)
    def check_pose():
        if max(errors(device.read(),cfg['home']['target']).values())>cfg['home']['arrival_tolerance']:
            raise ConfigError('Basket inspection/reference capture requires strict registered Home')
    check_pose()
    spec=options(cfg)
    # Reject stale reference geometry before paying camera connection/warmup cost.
    inspector=None if capture else BasketInspector(cfg)
    camera=camera_for(cfg)
    try:
        opened=time.perf_counter()
        print(f"[BASKET] 카메라 연결/준비 중 (설정 warmup={cfg['hardware']['cameras'][spec['camera']].get('warmup_s',0)}초). watch는 한 번 연결 후 계속 검사합니다.",flush=True)
        camera.connect()
        connection_s=time.perf_counter()-opened
        print(f'[BASKET] 카메라 준비 완료: {connection_s:.2f}초',flush=True)
        if capture=='snapshot':
            frame=camera.read_latest().copy();check_pose();return frame
        if capture:
            samples=[];last=None;deadline=time.perf_counter()+spec['inspect_timeout_s']
            while time.perf_counter()<deadline and len(samples)<spec['stable_samples']:
                frame,stamp=read_camera(camera,spec['max_frame_age_s'])
                if stamp is not None and (last is None or stamp>last) and 0<=time.perf_counter()-stamp<=.5:
                    image=crop(frame,spec)
                    if samples and float(np.abs(image.astype(float)-samples[-1]).mean())>spec['stability_delta']:
                        samples=[]
                    samples.append(image);last=stamp
                time.sleep(spec['sample_interval_s'])
            if len(samples)<spec['stable_samples']: raise ConfigError('Empty reference camera not stable/fresh')
            image=np.median(np.stack(samples),axis=0).astype(np.uint8)
            check_pose()
            return image
        deadline=time.perf_counter()+spec['inspect_timeout_s']
        iterations=0;last_print=None;next_print=0.
        while watch or time.perf_counter()<deadline:
            check_pose()
            try:
                frame,stamp=read_camera(camera,spec['max_frame_age_s'])
                result=inspector.update(frame,time.perf_counter(),stamp)
            except TimeoutError:
                result=inspector.update(None,time.perf_counter(),None)
            snapshot=inspector.snapshot(time.time())
            snapshot.update(connection_s=connection_s,elapsed_s=time.perf_counter()-opened)
            if watch:
                tag=(snapshot['verdict'],snapshot['candidate'],snapshot['reason'])
                if tag!=last_print or time.perf_counter()>=next_print:
                    print(__import__('json').dumps(snapshot,ensure_ascii=False),flush=True)
                    last_print=tag;next_print=time.perf_counter()+1
                iterations+=1
                if samples_limit and iterations>=samples_limit:return snapshot
            if result!='UNKNOWN':
                if not watch:return snapshot
            time.sleep(spec['sample_interval_s'])
        return inspector.snapshot(time.time())
    finally:
        if camera.is_connected: camera.disconnect()
