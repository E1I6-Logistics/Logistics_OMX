"""Small fixed-camera ROI comparison. No neural model, no object-count claim."""
import time
from .config import ConfigError, number, only_keys, path_value
from .rgb_roi import DEFAULTS as RGB_DEFAULTS
from .exposure import DEFAULTS as EXPOSURE_DEFAULTS
from .basket_registration import DEFAULTS as REGISTRATION_DEFAULTS

DEFAULTS = dict(camera="top", roi=None, reference=None, reference_binding=None,
                pixel_threshold=25, empty_ratio=.01, occupied_ratio=.04,
                illumination_limit=18, stability_delta=3., sample_interval_s=.1,
                stable_samples=3,confirmation_s=.2,max_frame_age_s=.25,inspect_timeout_s=12., max_attempts=16,
                max_no_change=2, job_timeout_s=900., require_quantity_match=True,**RGB_DEFAULTS,**EXPOSURE_DEFAULTS,**REGISTRATION_DEFAULTS)


def options(cfg): return dict(DEFAULTS, **cfg.get("unload", {}))


def validate_unload(cfg):
    spec = cfg.get("unload", {})
    if not isinstance(spec, dict): raise ConfigError("unload must be a mapping")
    only_keys(spec, " ".join(DEFAULTS), "unload")
    spec = options(cfg)
    if spec["camera"] not in cfg["hardware"]["cameras"] and cfg["role"] == "unload":
        raise ConfigError("unload.camera must be an existing fixed camera")
    roi = spec["roi"]
    if roi is not None:
        if not isinstance(roi, list) or len(roi) != 4 or any(type(v) is not int for v in roi):
            raise ConfigError("unload.roi must be [x,y,width,height] pixels")
        x, y, w, h = roi
        cam = cfg["hardware"]["cameras"].get(spec["camera"], {})
        if min(x,y) < 0 or min(w,h) < 8 or x+w > cam.get("width",0) or y+h > cam.get("height",0):
            raise ConfigError("Basket ROI outside camera image")
    for key, lo, hi in (("pixel_threshold",1,254),("empty_ratio",0,.5),("occupied_ratio",.001,1),
                        ("illumination_limit",1,100),("stability_delta",.1,30),("sample_interval_s",.05,2),
                        ("confirmation_s",.1,3),("max_frame_age_s",.05,.5),
                        ("inspect_timeout_s",2,120),("job_timeout_s",10,3600)):
        number(spec[key],"unload."+key,lo,hi)
    if spec["empty_ratio"] >= spec["occupied_ratio"]: raise ConfigError("Empty/occupied thresholds must have an uncertainty gap")
    for key, lo, hi in (("stable_samples",3,30),("max_attempts",1,64),("max_no_change",1,10)):
        if type(spec[key]) is not int or not lo <= spec[key] <= hi: raise ConfigError("Invalid unload."+key)
    if type(spec["require_quantity_match"]) is not bool: raise ConfigError("require_quantity_match must be boolean")
    if spec["stable_samples"] * spec["sample_interval_s"] >= spec["inspect_timeout_s"]:
        raise ConfigError("Inspection timeout must exceed sample window")
    if spec['confirmation_s']>=spec['inspect_timeout_s']:raise ConfigError('Confirmation window exceeds inspection timeout')
    from .rgb_roi import validate
    validate(spec)
    from .exposure import validate as validate_exposure
    validate_exposure(spec)
    from .basket_registration import validate as validate_registration
    validate_registration(spec)
    if spec['vision_method']=='low_template' and not spec['low_exposure_enabled']:
        raise ConfigError('low_template requires inspection-only low exposure')
    if spec['low_exposure_enabled'] and spec['vision_method']!='low_template':
        raise ConfigError('Low exposure uses low_template comparison; configure omx_basket low VALUE')


def crop(image, spec):
    import cv2
    import numpy as np
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ConfigError("Expected uint8 RGB camera image")
    if spec.get("registration_enabled"):
        from .basket_registration import Tracker
        return Tracker(spec).extract(image)
    if spec["roi"] is None: raise ConfigError("Register a basket ROI first")
    x,y,w,h = spec["roi"]
    if x < 0 or y < 0 or min(w,h)<8 or y+h>image.shape[0] or x+w>image.shape[1]:
        raise ConfigError("Basket ROI does not fit current image")
    # Fixed small analysis image; policy images are never resized or masked.
    from .rgb_roi import SIZE
    size=SIZE if spec.get('vision_method') in ('rgb_roi','low_template') else (160,120)
    small = cv2.resize(image[y:y+h,x:x+w], size, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(small,(3,3),0)


def changed_ratio(before, after, threshold):
    import numpy as np
    return float((np.max(np.abs(before.astype(np.int16)-after.astype(np.int16)),axis=2)>=threshold).mean())


class BasketInspector:
    def __init__(self, cfg):
        import numpy as np
        from .workflow import registration_binding
        self.spec = options(cfg)
        if (self.spec["reference_binding"] != registration_binding(cfg)
                or not self.spec["reference"] or self.spec["roi"] is None):
            raise ConfigError("Empty basket reference/ROI not registered for current Home and camera")
        with np.load(path_value(self.spec["reference"]), allow_pickle=False) as data:
            self.reference = data["image"].copy()
            roi = data["roi"].tolist()
            saved_signature=str(data['roi_signature'].item()) if 'roi_signature' in data else None
            import json
            self.exposure_profile=json.loads(str(data['exposure_profile'].item())) if 'exposure_profile' in data else None
            self.registration_profile=json.loads(str(data['registration_profile'].item())) if 'registration_profile' in data else None
            self.reference_valid_mask=data['registration_mask'].astype(bool) if 'registration_mask' in data else None
        if self.spec['low_exposure_enabled']:
            from .exposure import validate_profile
            validate_profile(self.exposure_profile,self.spec)
        elif self.exposure_profile is not None:raise ConfigError('Low-exposure reference cannot be compared with normal camera frames')
        enhanced=self.spec['vision_method'] in ('rgb_roi','low_template')
        self.tracker=None
        if self.spec['registration_enabled']:
            from .basket_registration import Tracker,validate_profile
            camera=cfg['hardware']['cameras'][self.spec['camera']]
            validate_profile(self.registration_profile,self.spec,(camera['height'],camera['width']))
            if self.spec['registration_allow_partial'] and self.reference_valid_mask is None:raise ConfigError('Partial view requires a new visible-area reference')
            self.tracker=Tracker(self.spec,self.registration_profile)
        elif self.registration_profile is not None:raise ConfigError('Registration reference requires alignment mode; recapture reference')
        shape=(128,128,3) if self.tracker else (240,320,3) if enhanced else (120,160,3)
        if self.reference.shape != shape or self.reference.dtype != np.uint8 or roi != self.spec["roi"]:
            raise ConfigError("Empty reference image/ROI mismatch; register again")
        self.comparator=None
        if enhanced:
            from .rgb_roi import Comparator,signature
            if saved_signature!=signature(self.spec):raise ConfigError('RGB ROI/polygon reference changed; run omx_basket capture with an empty basket')
            if self.tracker:
                from .basket_registration import Comparator as RegisteredComparator
                self.comparator=RegisteredComparator(self.reference,self.spec,self.reference_valid_mask)
            else:self.comparator=Comparator(self.reference,self.spec)
        self.reference16=self.reference.astype(np.int16)
        self.reference_mean=self.reference.mean(axis=(0,1))
        self.reset()

    def reset(self):
        self.previous = None
        self.last_sample = None
        self.last_frame = None
        self.verdict = None
        self.samples = 0
        self.result = "UNKNOWN"
        self.ratio = None
        self.latest = None
        self.since=None
        self.reason='WAIT_FRAME';self.processing_ms=0.;self.frame_age_ms=None
        self.evidence={}

    def update(self, image, now, frame_timestamp):
        import numpy as np
        number(now,"inspection timestamp",0)
        self.result='UNKNOWN'
        self.frame_age_ms=None if frame_timestamp is None else (now-frame_timestamp)*1000
        if frame_timestamp is None or not 0<=now-frame_timestamp<=self.spec['max_frame_age_s']:
            self.samples=0;self.since=None;self.verdict=None;self.reason='STALE_FRAME'
            return self.result
        if self.last_frame is not None and frame_timestamp<=self.last_frame:
            self.reason='WAIT_NEW_FRAME'
            return self.result
        if self.last_sample is not None and now-self.last_sample+1e-9 < self.spec["sample_interval_s"]:
            self.reason='WAIT_INTERVAL'
            return "UNKNOWN"
        if self.last_sample is not None and not 0 < now-self.last_sample <= 2*self.spec["sample_interval_s"]+.1:
            self.samples=0;self.since=None
        begun=time.perf_counter()
        if self.tracker:
            from .basket_registration import ViewError
            try:current=self.tracker.extract(image)
            except ViewError as exc:
                self.samples=0;self.since=None;self.verdict=None;self.previous=None;self.latest=None;self.ratio=None
                self.last_sample=now;self.last_frame=frame_timestamp;self.reason=str(exc)
                self.evidence=dict(quality_flags=[str(exc)],registration='REJECTED',features_computed=False)
                self.processing_ms=(time.perf_counter()-begun)*1000
                return 'UNKNOWN'
        else:current=crop(image,self.spec)
        self.latest=current
        self.last_sample=now; self.last_frame=frame_timestamp
        # Reuse immutable reference statistics and one small int16 ROI buffer.
        # Full camera frames stay in the camera's same-process latest-frame buffer.
        current16=current.astype(np.int16)
        if self.comparator:
            self.evidence=self.comparator.compare(current,self.tracker.valid_mask) if self.tracker else self.comparator.compare(current)
            if self.tracker:self.evidence.update(self.tracker.last_info)
            verdict=self.evidence['verdict'];self.ratio=self.evidence['changed_ratio']
        else:
            self.ratio=float((np.max(np.abs(current16-self.reference16),axis=2)>=self.spec['pixel_threshold']).mean())
            illumination=float(np.abs(current.mean(axis=(0,1))-self.reference_mean).max())
            verdict = ('EMPTY' if self.ratio<=self.spec['empty_ratio'] else 'OCCUPIED' if self.ratio>=self.spec['occupied_ratio'] else 'UNKNOWN')
            if illumination>self.spec['illumination_limit']:verdict='UNKNOWN'
        difference=None if self.previous is None else np.abs(current16-self.previous)
        stable = difference is not None and float(difference[self.comparator.mask].mean() if self.comparator else difference.mean()) <= self.spec["stability_delta"]
        if self.tracker:stable=stable and self.tracker.last_info['pose_stable']
        continuing=stable and verdict==self.verdict and verdict!='UNKNOWN'
        self.samples=self.samples+1 if continuing else 1 if verdict!='UNKNOWN' else 0
        self.since=self.since if continuing and self.since is not None else now if verdict!='UNKNOWN' else None
        self.previous=current16; self.verdict=verdict
        held=self.since is not None and now-self.since+1e-9>=self.spec['confirmation_s']
        self.result=verdict if self.samples>=self.spec['stable_samples'] and held else 'UNKNOWN'
        self.reason='CONFIRMED' if self.result!='UNKNOWN' else 'STABILIZING' if verdict!='UNKNOWN' else 'LIGHTING_OR_UNCERTAIN'
        if self.comparator and verdict=='UNKNOWN':self.reason=self.evidence['evidence_reason']
        self.processing_ms=(time.perf_counter()-begun)*1000
        return self.result

    def snapshot(self,now):
        return dict(verdict=self.result,candidate=self.verdict or 'UNKNOWN',reason=self.reason,
                    changed_ratio=self.ratio,stable_samples=self.samples,frame_age_ms=self.frame_age_ms,
                    processing_ms=self.processing_ms,at=now,exact_item_count=None,
                    **{k:v for k,v in self.evidence.items() if k not in ('verdict','changed_ratio')})

    def transfer_changed(self,before,after):
        if not self.comparator:return changed_ratio(before,after,self.spec['pixel_threshold'])>=self.spec['occupied_ratio']
        from .rgb_roi import Comparator
        spec=dict(self.spec,full_view_confirmed=True)
        if self.tracker:
            from .basket_registration import Comparator as RegisteredComparator
            evidence=RegisteredComparator(before,spec,self.comparator.reference_mask).compare(after,(before.max(2)>0)&(after.max(2)>0))
        else:evidence=Comparator(before,spec).compare(after)
        return evidence['verdict']=='OCCUPIED'


def read_camera(camera,max_age_s=.25):
    """Pair the actual sampled image with its timestamp, not a newer frame's stamp."""
    paired=getattr(camera,'read_latest_with_timestamp',None)
    if callable(paired):image,stamp=paired(max_age_ms=max_age_s*1000)
    else:
        before=getattr(camera,'latest_timestamp',None)
        image=camera.read_latest(max_age_ms=max_age_s*1000)
        stamp=getattr(camera,'latest_timestamp',None)
        if before!=stamp:raise TimeoutError('Camera snapshot changed without atomic timestamp support')
    now=time.perf_counter()
    if stamp is None or not 0<=now-stamp<=max_age_s:raise TimeoutError('Basket camera frame is stale')
    return image,stamp
