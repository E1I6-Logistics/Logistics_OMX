"""Pure home/counting logic, shared by the tool and actual rollout."""
import math
from .config import JOINTS, number, ConfigError

def joints(observation):
    values = {}
    for j in JOINTS:
        value = observation.get(j + ".pos", observation.get(j))
        values[j] = number(value, j, 0 if j == "gripper" else -100, 100)
    return values

def errors(observation, target):
    q = joints(observation)
    return {j: abs(q[j] - target[j]) for j in JOINTS}

class HomeDetector:
    """One completion per departure; gaps never count as observed hold time."""
    def __init__(self, cfg, now=0., hold=None):
        self.home = cfg["home"]; self.counting = cfg["counting"]
        self.target = self.home["target"]
        if self.target is None: raise ConfigError("Home required")
        self.hold = self.home["hold_s"] if hold is None else hold
        self.reset(now)

    def reset(self, now):
        self.started = now; self.departed = False; self.previous = None
        self.last = None; self.since = None; self.near = None; self.best = None
        self.stalled = False; self.rows = {}

    def invalidate(self, now):
        self.previous = None; self.last = None; self.since = None; self.near = None; self.best = None
        self.stalled = False

    def update(self, observation, now):
        try: q = joints(observation)
        except (ValueError, TypeError):
            self.invalidate(now)
            raise
        if not math.isfinite(now): raise ConfigError("Invalid sample timestamp")
        gap = self.last is None or now <= self.last or now - self.last > self.home["max_sample_gap_s"]
        if gap:
            self.since = None; self.near = None; self.best = None; self.previous = None
        self.last = now
        err = {j: abs(q[j] - self.target[j]) for j in JOINTS}
        maximum = max(err.values())
        self.rows = {j: {"target": self.target[j], "current": q[j], "error": err[j], "tolerance": self.home["tolerance"][j], "ok": err[j] <= self.home["tolerance"][j]} for j in JOINTS}
        # Departure is observed immediately; min_cycle gates completion, not arming.
        if maximum >= self.counting["leave_tolerance"]: self.departed = True
        inside = all(row["ok"] for row in self.rows.values())
        still = self.previous is not None and max(abs(q[j] - self.previous[j]) for j in JOINTS) <= self.home["still_delta"]
        if inside and still and self.departed:
            if self.since is None: self.since = now
        else: self.since = None
        self.previous = q
        # Progress-sensitive diagnostic; never raises just for dwelling near Home.
        near = self.departed and not inside and maximum <= 2 * self.counting["leave_tolerance"]
        if near:
            if self.best is None or maximum < self.best - self.counting["progress_epsilon"]:
                self.best = maximum; self.near = now
            self.stalled = self.near is not None and now - self.near >= self.counting["stall_s"]
        else:
            self.near = None; self.best = None; self.stalled = False
        if self.since is not None and now - self.since >= self.hold and now - self.started >= self.counting["min_cycle_s"]:
            self.reset(now)
            return True
        return False

def stable_pose(samples, max_span):
    if len(samples) < 3: raise ConfigError("At least three samples required")
    qs = [joints(s) for s in samples]
    for j in JOINTS:
        if max(q[j] for q in qs) - min(q[j] for q in qs) > max_span: raise ConfigError(f"Pose is moving: {j}; capture rejected")
    return {j: sum(q[j] for q in qs) / len(qs) for j in JOINTS}

def move_home(device, cfg, *, clock, sleep, stopped=lambda: False):
    """Small steps, bounded starting distance, fresh feedback. Never enables torque."""
    h = cfg["home"]; target = h["target"]
    if target is None: raise ConfigError("No registered home")
    q = joints(device.read())
    if max(errors(q, target).values()) > h["max_start_error"]:
        raise ConfigError("Outside approved Home approach area; position arm locally first")
    start = previous = clock(); stable_since = None; last_q = q
    while True:
        if stopped(): raise InterruptedError("Stopped; no further home actions")
        now = clock()
        if now - start > h["move_timeout_s"]: raise TimeoutError("Home movement timed out")
        q = joints(device.read())
        now = clock()
        if now - start > h["move_timeout_s"]: raise TimeoutError("Home movement timed out")
        if now - previous > h["max_sample_gap_s"]: raise TimeoutError("Home feedback stalled; no next action")
        err = errors(q, target)
        still = max(abs(q[j] - last_q[j]) for j in JOINTS) <= h["still_delta"]
        if all(err[j] <= h["arrival_tolerance"] for j in JOINTS) and still:
            stable_since = now if stable_since is None else stable_since
            if now - stable_since >= h["hold_s"]: return q
        else: stable_since = None
        delta_t = now - previous
        if delta_t > h["max_sample_gap_s"]: raise TimeoutError("Home feedback stalled; no next action")
        step = min(h["max_step"], h["move_speed"] * max(0., delta_t))
        command = {j: q[j] + max(-step, min(step, target[j] - q[j])) for j in JOINTS}
        if stopped(): raise InterruptedError("Stopped before send")
        device.send(command)
        previous = now; last_q = q
        sleep(.05)
