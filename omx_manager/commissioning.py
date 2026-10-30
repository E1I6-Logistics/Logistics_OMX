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
    if cfg['role']=='unload': result.setdefault('unload',dict(DEFAULTS))
    for key,value in dict(auto_home=True,poll_s=5.,routes={}).items(): result['recovery'].setdefault(key,value)
    return result


def require_commissioned(cfg):
    require_home(cfg)
    missing=[]
    for item in profile(cfg)['items']:
        try: require_stages(cfg,item)
        except ConfigError as exc: missing.append(str(exc))
    if cfg['role']=='unload':
        from .basket import BasketInspector
        try: BasketInspector(cfg)
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


def basket_operation(cfg,device,*,capture=False):
    import numpy as np
    from .basket import options,crop,BasketInspector
    require_home(cfg)
    def check_pose():
        if max(errors(device.read(),cfg['home']['target']).values())>cfg['home']['arrival_tolerance']:
            raise ConfigError('Basket inspection/reference capture requires strict registered Home')
    check_pose()
    spec=options(cfg)
    camera=camera_for(cfg)
    try:
        camera.connect()
        if capture=='snapshot':
            frame=camera.read_latest().copy();check_pose();return frame
        if capture:
            samples=[];last=None;deadline=time.perf_counter()+spec['inspect_timeout_s']
            while time.perf_counter()<deadline and len(samples)<spec['stable_samples']:
                stamp=getattr(camera,'latest_timestamp',None)
                if stamp is not None and (last is None or stamp>last) and 0<=time.perf_counter()-stamp<=.5:
                    image=crop(camera.read_latest(),spec)
                    if samples and float(np.abs(image.astype(float)-samples[-1]).mean())>spec['stability_delta']:
                        samples=[]
                    samples.append(image);last=stamp
                time.sleep(spec['sample_interval_s'])
            if len(samples)<spec['stable_samples']: raise ConfigError('Empty reference camera not stable/fresh')
            image=np.median(np.stack(samples),axis=0).astype(np.uint8)
            check_pose()
            return image
        inspector=BasketInspector(cfg)
        deadline=time.perf_counter()+spec['inspect_timeout_s']
        while time.perf_counter()<deadline:
            result=inspector.update(camera.read_latest(),time.perf_counter(),getattr(camera,'latest_timestamp',None))
            if result!='UNKNOWN':
                check_pose();return dict(verdict=result,changed_ratio=inspector.ratio,exact_item_count=None)
            time.sleep(spec['sample_interval_s'])
        return dict(verdict='UNKNOWN',changed_ratio=inspector.ratio,exact_item_count=None)
    finally:
        if camera.is_connected: camera.disconnect()
