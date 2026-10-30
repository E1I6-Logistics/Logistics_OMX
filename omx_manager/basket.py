"""Small fixed-camera ROI comparison. No neural model, no object-count claim."""
from .config import ConfigError, number, only_keys, path_value

DEFAULTS = dict(camera="top", roi=None, reference=None, reference_binding=None,
                pixel_threshold=25, empty_ratio=.01, occupied_ratio=.04,
                illumination_limit=18, stability_delta=3., sample_interval_s=.2,
                stable_samples=5, inspect_timeout_s=12., max_attempts=16,
                max_no_change=2, job_timeout_s=900., require_quantity_match=True)


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
                        ("inspect_timeout_s",2,120),("job_timeout_s",10,3600)):
        number(spec[key],"unload."+key,lo,hi)
    if spec["empty_ratio"] >= spec["occupied_ratio"]: raise ConfigError("Empty/occupied thresholds must have an uncertainty gap")
    for key, lo, hi in (("stable_samples",3,30),("max_attempts",1,64),("max_no_change",1,10)):
        if type(spec[key]) is not int or not lo <= spec[key] <= hi: raise ConfigError("Invalid unload."+key)
    if type(spec["require_quantity_match"]) is not bool: raise ConfigError("require_quantity_match must be boolean")
    if spec["stable_samples"] * spec["sample_interval_s"] >= spec["inspect_timeout_s"]:
        raise ConfigError("Inspection timeout must exceed sample window")


def crop(image, spec):
    import cv2
    import numpy as np
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ConfigError("Expected uint8 RGB camera image")
    if spec["roi"] is None: raise ConfigError("Register a basket ROI first")
    x,y,w,h = spec["roi"]
    if x < 0 or y < 0 or min(w,h)<8 or y+h>image.shape[0] or x+w>image.shape[1]:
        raise ConfigError("Basket ROI does not fit current image")
    # Fixed small analysis image; policy images are never resized or masked.
    small = cv2.resize(image[y:y+h,x:x+w], (160,120), interpolation=cv2.INTER_AREA)
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
        if self.reference.shape != (120,160,3) or self.reference.dtype != np.uint8 or roi != self.spec["roi"]:
            raise ConfigError("Empty reference image/ROI mismatch; register again")
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

    def update(self, image, now, frame_timestamp):
        import numpy as np
        number(now,"inspection timestamp",0)
        if (frame_timestamp is None or not 0 <= now-frame_timestamp <= .5
                or (self.last_frame is not None and frame_timestamp <= self.last_frame)):
            self.samples=0; self.result="UNKNOWN"
            return self.result
        if self.last_sample is not None and now-self.last_sample < self.spec["sample_interval_s"]:
            return "UNKNOWN"
        if self.last_sample is not None and not 0 < now-self.last_sample <= 2*self.spec["sample_interval_s"]+.1:
            self.samples=0
        current = crop(image,self.spec)
        self.latest=current
        self.last_sample=now; self.last_frame=frame_timestamp
        self.ratio=changed_ratio(self.reference,current,self.spec["pixel_threshold"])
        illumination=float(np.abs(current.astype(float).mean(axis=(0,1))-self.reference.mean(axis=(0,1))).max())
        verdict = ("EMPTY" if self.ratio <= self.spec["empty_ratio"] else
                   "OCCUPIED" if self.ratio >= self.spec["occupied_ratio"] else "UNKNOWN")
        if illumination > self.spec["illumination_limit"]: verdict="UNKNOWN"
        stable = self.previous is not None and float(np.abs(current.astype(float)-self.previous).mean()) <= self.spec["stability_delta"]
        self.samples = self.samples+1 if stable and verdict == self.verdict and verdict != "UNKNOWN" else 1 if verdict != "UNKNOWN" else 0
        self.previous=current; self.verdict=verdict
        self.result=verdict if self.samples >= self.spec["stable_samples"] else "UNKNOWN"
        return self.result
