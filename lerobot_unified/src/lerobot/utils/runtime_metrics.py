"""Opt-in, image-free OMX counters. No disk I/O on capture/control threads."""
from functools import wraps
from collections import deque
import json
import math
import os
from pathlib import Path
import threading
import time

ENABLED = bool(os.environ.get("OMX_METRICS_PATH"))

class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = {}
        self.timings = {}
        self.actions = deque(maxlen=200)
    def event(self, name, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            count, _ = self.events.get(name, (0, None))
            self.events[name] = (count + 1, now)
    def duration(self, name, seconds):
        with self.lock:
            n, total, worst = self.timings.get(name, (0, 0., 0.))
            self.timings[name] = (n + 1, total + seconds, max(worst, seconds))
    def snapshot(self):
        with self.lock:
            result={"events": dict(self.events), "timings": dict(self.timings)}
            actions=list(self.actions)
        if actions:
            latest=actions[-1]
            result['actions']=dict(latest,window_samples=len(actions),window_s=latest['at_monotonic']-actions[0]['at_monotonic'],
                sent_span={k:max(a['sent'][k] for a in actions)-min(a['sent'][k] for a in actions) for k in latest['sent']},
                observed_span={k:max(a['observed_before_inference'][k] for a in actions)-min(a['observed_before_inference'][k] for a in actions) for k in latest['sent']})
        return result

    def action(self,predicted,processed,sent,observed):
        keys=tuple(sorted(k for k in sent if k.endswith('.pos')))
        if not keys:return
        maps=[{k:float(data[k]) for k in keys} for data in (predicted,processed,sent,observed)]
        if not all(math.isfinite(v) for data in maps for v in data.values()):return
        row=dict(at_monotonic=time.monotonic(),predicted=maps[0],processed=maps[1],sent=maps[2],observed_before_inference=maps[3])
        with self.lock:
            # Do not combine differently shaped robots/episodes in one range.
            if self.actions and self.actions[-1]['sent'].keys()!=row['sent'].keys():self.actions.clear()
            self.actions.append(row)

COUNTERS = Counters()

def event(name):
    if ENABLED:
        COUNTERS.event(name)

def action_diagnostic(predicted,processed,sent,observed):
    if not ENABLED:return
    try:COUNTERS.action(predicted,processed,sent,observed)
    except (KeyError,TypeError,ValueError,AttributeError):pass

def camera_event(camera, kind):
    if ENABLED:
        name = getattr(camera, "_omx_metric_name", None)
        if name is not None:
            COUNTERS.event("camera/" + name + "/" + kind)

def measure_chunk(function):
    @wraps(function)
    def measured(*args, **kwargs):
        if not ENABLED:
            return function(*args, **kwargs)
        start = time.monotonic()
        try:
            return function(*args, **kwargs)
        finally:
            COUNTERS.duration("act_chunk", time.monotonic() - start)
    return measured

def start_metrics(robot, shutdown_event, target_hz, interval=None, *,policy=None,policy_path=None):
    """A single daemon writes small snapshots. It never polls hardware."""
    if not ENABLED:
        return
    if interval is None: interval=float(os.environ.get("OMX_METRICS_INTERVAL_S","2"))
    path = Path(os.environ["OMX_METRICS_PATH"])
    cameras = getattr(robot, "cameras", {})
    camera_config = {}
    from omx_manager.cpu import current_threads
    cpu_threads=current_threads()
    config=getattr(policy,'config',None)
    policy_info=dict(initial_path=str(policy_path or getattr(config,'pretrained_path',None)) if policy_path is not None or getattr(config,'pretrained_path',None) is not None else None,
                     type=getattr(config,'type',None),chunk_size=getattr(config,'chunk_size',None),
                     n_action_steps=getattr(config,'n_action_steps',None),temporal_ensemble_coeff=getattr(config,'temporal_ensemble_coeff',None),
                     input_features=list(getattr(config,'input_features',{})))
    for name, camera in cameras.items():
        camera._omx_metric_name = name
        camera_config[name] = {"configured_fps": getattr(camera, "fps", None),
                               "processing_fps": getattr(camera, "processing_fps", None)}
    try:
        stat = Path("/proc/self/stat").read_text()
        start_ticks = int(stat[stat.rfind(")")+2:].split()[19])
    except (OSError, ValueError, IndexError):
        start_ticks = None
    stop = threading.Event()
    def report():
        temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
        warned = False
        try:
            while not stop.is_set() and not shutdown_event.is_set():
                try:
                    data = dict(COUNTERS.snapshot(), pid=os.getpid(), process_start_ticks=start_ticks, monotonic=time.monotonic(),
                                target_hz=target_hz, cameras=camera_config,cpu_threads=cpu_threads,policy=policy_info)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary.write_text(json.dumps(data, allow_nan=False))
                    os.replace(temporary, path)  # diagnostics: intentionally no fsync
                except Exception as exc:
                    if not warned:
                        print("[DIAGNOSTICS] Snapshot unavailable: " + str(exc), flush=True)
                        warned = True
                if stop.wait(interval):
                    break
        finally:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass
    thread = threading.Thread(target=report, name="omx-metrics", daemon=True)
    def close():
        stop.set()
        thread.join(timeout=1.)
    robot._omx_metrics_close = close
    thread.start()
