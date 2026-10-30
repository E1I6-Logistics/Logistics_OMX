"""Pure home/counting logic, shared by the tool and actual rollout."""
import logging
import math
from .config import JOINTS, number, ConfigError

logger = logging.getLogger(__name__)

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
        self.last_gap_s = None; self.gap_reset = False; self.still = False

    @property
    def in_home_window(self):
        return self.departed and bool(self.rows) and all(row["ok"] for row in self.rows.values())

    def invalidate(self, now):
        self.previous = None; self.last = None; self.since = None; self.near = None; self.best = None
        self.stalled = False
        self.last_gap_s = None; self.gap_reset = True; self.still = False

    def update(self, observation, now, eligible=True):
        try: q = joints(observation)
        except (ValueError, TypeError):
            self.invalidate(now)
            raise
        if not math.isfinite(now): raise ConfigError("Invalid sample timestamp")
        self.last_gap_s = None if self.last is None else now - self.last
        gap = self.last is None or now <= self.last or now - self.last > self.home["max_sample_gap_s"]
        self.gap_reset = gap
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
        self.still = still
        if inside and still and self.departed and eligible:
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
            completed = {"departed": self.departed, "still": self.still,
                         "hold_since": self.since, "sample_gap_s": self.last_gap_s,
                         "gap_reset": self.gap_reset, "in_home_window": self.in_home_window}
            self.reset(now)
            self.last_completion = completed
            return True
        return False

def stable_pose(samples, max_span):
    if len(samples) < 3: raise ConfigError("At least three samples required")
    qs = [joints(s) for s in samples]
    for j in JOINTS:
        if max(q[j] for q in qs) - min(q[j] for q in qs) > max_span: raise ConfigError(f"Pose is moving: {j}; capture rejected")
    return {j: sum(q[j] for q in qs) / len(qs) for j in JOINTS}

def move_home(device, cfg, *, clock, sleep, stopped=lambda: False, report=None, guard=None):
    """Ramp the commanded goal; bound speed, feedback lag and the approach area.

    A goal relative to each new measurement can stay inside the servo deadband
    forever. Retaining the previous goal lets the position controller overcome
    that deadband without making an unbounded jump away from measured feedback.
    """
    h = cfg["home"]; target = h["target"]
    if target is None: raise ConfigError("No registered home")
    q = joints(device.read())
    start = previous = clock(); stable_since = None; last_q = q
    goal = q.copy()
    following_limit = min(h.get("max_following_error", 3.0), h["max_start_error"])
    next_report = start
    report_warned = False

    def details():
        err = errors(q, target)
        return "; ".join(
            f"{joint} error={err[joint]:.3f} current={q[joint]:.3f} "
            f"target={target[joint]:.3f} tolerance={h['arrival_tolerance']:.3f}"
            for joint in sorted(JOINTS, key=lambda name: err[name], reverse=True)
            if err[joint] > h["arrival_tolerance"]
        ) or ("all joints within arrival tolerance; waiting for continuous stillness; " +
              "; ".join(f"{joint} step_delta={abs(q[joint]-last_q[joint]):.3f}" for joint in JOINTS))

    def emit(phase, now, reason=None, force=False):
        nonlocal next_report, report_warned
        if report is None or (not force and now < next_report): return
        next_report = now + 1.0
        rows = {joint: {"current": q[joint], "target": target[joint],
                       "error": abs(q[joint] - target[joint]),
                       "tolerance": h["arrival_tolerance"],
                       "count_tolerance": h["tolerance"][joint],
                       "goal": goal[joint], "following_error": abs(goal[joint] - q[joint]),
                       "movement_delta": abs(q[joint]-last_q[joint]),
                       "ok": abs(q[joint] - target[joint]) <= h["arrival_tolerance"]}
                for joint in JOINTS}
        try:
            report({"phase": phase, "monotonic": now, "elapsed_s": now - start,
                    "joints": rows, "max_following_error": following_limit, "reason": reason})
        except Exception as exc:
            if not report_warned:
                logger.warning("Home diagnostics unavailable (motion unchanged): %s", exc)
                report_warned = True

    try:
        emit("STARTING", start, force=True)
        if stopped(): raise InterruptedError("Stopped; no Home movement")
        if guard: guard(q)
        if max(errors(q, target).values()) > h["max_start_error"]:
            raise ConfigError("Outside approved Home approach area; position arm locally first; " + details())
        while True:
            if stopped(): raise InterruptedError("Stopped; no further home actions")
            now = clock()
            if now - start > h["move_timeout_s"]:
                raise TimeoutError("Home movement timed out: " + details())
            q = joints(device.read())
            now = clock()
            if now - start > h["move_timeout_s"]:
                raise TimeoutError("Home movement timed out: " + details())
            delta_t = now - previous
            if delta_t < 0 or delta_t > h["max_sample_gap_s"]:
                raise TimeoutError("Home feedback stalled; no next action; " + details())
            err = errors(q, target)
            if guard: guard(q)
            if max(err.values()) > h["max_start_error"]:
                raise ConfigError("Feedback left approved Home approach area; " + details())
            if max(abs(goal[joint] - q[joint]) for joint in JOINTS) > following_limit + 1e-9:
                raise RuntimeError("Home following error exceeded limit; " + details())
            still = max(abs(q[joint] - last_q[joint]) for joint in JOINTS) <= h["still_delta"]
            if all(err[joint] <= h["arrival_tolerance"] for joint in JOINTS) and still:
                stable_since = now if stable_since is None else stable_since
                if now - stable_since >= h["hold_s"]:
                    emit("ARRIVED", now, force=True)
                    return q
            else: stable_since = None
            step = min(h["max_step"], h["move_speed"] * delta_t)
            command = {}
            span = max(abs(target[j] - goal[j]) for j in JOINTS)
            for joint in JOINTS:
                delta = target[joint] - goal[joint]
                # Tested route segments advance all joints at one common fraction.
                candidate = goal[joint] + (delta * min(1., step / span) if guard and span else max(-step, min(step, delta)))
                # Wait for a lagging joint before advancing its reference again.
                command[joint] = candidate if abs(candidate - q[joint]) <= following_limit else goal[joint]
            if stopped(): raise InterruptedError("Stopped before send")
            if guard: guard(command)
            device.send(command)
            goal = command
            emit("MOVING", now)
            previous = now; last_q = q
            sleep(.05)
    except BaseException as exc:
        emit("STOPPED" if isinstance(exc, (InterruptedError, KeyboardInterrupt)) else "FAILED",
             clock(), reason=str(exc), force=True)
        raise
