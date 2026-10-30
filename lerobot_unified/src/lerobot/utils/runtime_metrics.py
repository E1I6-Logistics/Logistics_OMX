"""Opt-in, image-free OMX counters. No disk I/O on capture/control threads."""
from functools import wraps
import json
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
            return {"events": dict(self.events), "timings": dict(self.timings)}

COUNTERS = Counters()

def event(name):
    if ENABLED:
        COUNTERS.event(name)

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

def start_metrics(robot, shutdown_event, target_hz, interval=None):
    """A single daemon writes small snapshots. It never polls hardware."""
    if not ENABLED:
        return
    if interval is None: interval=float(os.environ.get("OMX_METRICS_INTERVAL_S","2"))
    path = Path(os.environ["OMX_METRICS_PATH"])
    cameras = getattr(robot, "cameras", {})
    camera_config = {}
    from omx_manager.cpu import current_threads
    cpu_threads=current_threads()
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
                                target_hz=target_hz, cameras=camera_config,cpu_threads=cpu_threads)
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
