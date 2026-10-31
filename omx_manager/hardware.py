"""Follower-only bus access. NO leader, calibration write, mode write or torque enabling."""
import fcntl
import hashlib
import logging
import sys
from pathlib import Path
from .config import ROOT, JOINTS, path_value, check_files

logger = logging.getLogger(__name__)

def _close_port_if_open(bus):
    # SDK closePort() dereferences ser even when openPort() never succeeded.
    if bus.port_handler.is_open:
        bus.port_handler.closePort()

class Lock:
    def __init__(self, path): self.path = Path(path); self.fd = None
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = self.path.open("a+")
        try: fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.fd.close(); self.fd = None
            raise RuntimeError(f"Another runtime/calibration session owns {self.path}")
        return self
    def __exit__(self, *_):
        if self.fd:
            self.fd.close(); self.fd = None

def port_lock(port):
    key = hashlib.sha256(str(Path(port).resolve()).encode()).hexdigest()[:24]
    return Lock(ROOT / "state" / "locks" / ("port_" + key + ".lock"))

def cleanup_resources(engine=None, robot=None):
    """Each cleanup runs independently, even after a partial connection or failed stop."""
    failures = []
    def attempt(label, action):
        try: action()
        except Exception as exc: failures.append(f"{label}: {exc}")
    if robot is not None:
        close_metrics = getattr(robot, "_omx_metrics_close", None)
        if callable(close_metrics): attempt("diagnostics", close_metrics)
    if robot is not None:
        preview = getattr(robot, "_omx_view", None)
        if preview is not None: attempt("viewer", preview.close)
    if engine is not None: attempt("inference", engine.stop)
    if robot is not None:
        bus = getattr(robot, "bus", None)
        if bus is not None:
            hold = getattr(robot, 'hold_current_position', None)
            if callable(hold) and not robot.config.disable_torque_on_disconnect:
                attempt('motor_hold',hold)
        for name, camera in getattr(robot, "cameras", {}).items(): attempt("camera." + name, camera.disconnect)
        if bus is not None:
            def close_bus():
                try:
                    if bus.is_connected: bus.disconnect(disable_torque=robot.config.disable_torque_on_disconnect)
                finally: _close_port_if_open(bus)
            attempt("motor", close_bus)
        lease = getattr(robot, "_omx_port_lease", None)
        if lease is not None: attempt("port_lock", lambda: lease.__exit__(None, None, None))
    return failures

class FollowerBus:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bus = None
        self.robot = None
        self.lease = None
        self.motion_sent = False
    def __enter__(self):
        check_files(self.cfg, models=False)
        hw = self.cfg["hardware"]
        self.lease = port_lock(path_value(hw["port"]))
        self.lease.__enter__()
        try:
            from lerobot.robots.omx_follower.omx_follower import OmxFollower
            from lerobot.robots.omx_follower.config_omx_follower import OmxFollowerConfig
            cal = path_value(hw["calibration_file"])
            robot = OmxFollower(OmxFollowerConfig(port=str(path_value(hw["port"])), id=cal.stem, calibration_dir=cal.parent, cameras={}))
            # Keep the owner alive: Robot.__del__ disconnects its motor bus.
            self.robot = robot
            self.bus = robot.bus
            self.bus.connect()
            if not self.bus.is_calibrated: raise RuntimeError("Calibration differs from motors; automatic rewriting is forbidden")
            self.health()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
    def health(self):
        for j in JOINTS:
            status = self.bus.read("Hardware_Error_Status", j)
            if status: raise RuntimeError(f"Motor hardware error: {j}={status}; no motion")
    def release(self):
        # Torque only: never rewrite mode, offset, limits, calibration or goals.
        self.bus.disable_torque(num_retry=3)
        states = {joint: self.bus.read("Torque_Enable", joint) for joint in JOINTS}
        if any(value != 0 for value in states.values()):
            raise RuntimeError("Torque OFF verification failed; keep supporting the arm: " + str(states))
        return states
    def arm(self):
        from .config import require_home
        from .home import errors
        require_home(self.cfg)
        q = self.read()
        if max(errors(q, self.cfg["home"]["target"]).values()) > self.cfg["home"]["max_start_error"]:
            raise RuntimeError("Outside registered approach region; torque enabling refused")
        for j in JOINTS:
            expected = 5 if j == "gripper" else 4
            if self.bus.read("Operating_Mode", j) != expected:
                raise RuntimeError(f"{j}: mode mismatch; no automatic mode rewrite")
        # Never energize a stale goal left in motor RAM.
        self.bus.sync_write("Goal_Position", q)
        self.bus.enable_torque()
        after = self.read()
        if max(abs(after[j] - q[j]) for j in JOINTS) > self.cfg["home"]["arrival_tolerance"]:
            raise RuntimeError("Position changed at torque enable; inspect locally")
    def read(self):
        self.health()
        raw = self.bus.sync_read("Present_Position", normalize=False)
        for j in JOINTS:
            cal = self.bus.calibration[j]
            if not cal.range_min <= raw[j] <= cal.range_max:
                raise RuntimeError(f"{j}: raw position outside trained calibration range; no clamping allowed")
        return self.bus.sync_read("Present_Position")
    def send(self, values):
        self.health()
        # Pre-position the follower locally with torque enabled; the tool will never enable it.
        for j in JOINTS:
            if self.bus.read("Torque_Enable", j) != 1: raise RuntimeError(f"Torque OFF: {j}; tool will not enable it automatically")
        self.bus.sync_write("Goal_Position", values)
        self.motion_sent = True
    def hold_current_position(self):
        q=self.read()
        arms={joint:value for joint,value in q.items() if joint!='gripper'}
        for joint in arms:
            if self.bus.read('Torque_Enable',joint)!=1 or self.bus.read('Operating_Mode',joint)!=4:
                raise RuntimeError('Shutdown hold refused: torque/mode mismatch')
        self.bus.sync_write('Goal_Position',arms)
        logger.info('[HOLD] Tool motion ended; arm goal latched to measured pose, gripper preserved')
    def __exit__(self, exc_type, exc_value, traceback):
        failures = []
        try:
            if self.bus is not None:
                if self.motion_sent:
                    try:self.hold_current_position()
                    except Exception as exc:logger.warning('Final arm hold unavailable: %s',exc)
                _close_port_if_open(self.bus)
        except Exception as exc:
            failures.append(exc)
        finally:
            try:
                if self.lease:
                    self.lease.__exit__(exc_type, exc_value, traceback)
            except Exception as exc:
                failures.append(exc)
            finally:
                self.bus = None
                self.lease = None
                # Release the robot only after closing the port, without changing torque.
                self.robot = None
        if failures:
            if exc_type is None:
                raise failures[0]
            for failure in failures:
                logger.warning("OMX observation cleanup: %s", failure)
