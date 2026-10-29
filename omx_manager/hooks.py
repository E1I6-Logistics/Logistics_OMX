"""Small adapters used by the production rollout (no global hardware imports)."""
import logging
import os
from pathlib import Path
import time
from .config import load, require_home, write_json, path_value, ROOT
from .home import HomeDetector, move_home

logger=logging.getLogger(__name__)

def managed_config():
    value=os.environ.get("OMX_MANAGER_CONFIG")
    return load(value) if value else None

def initialize_home(robot, stopped=lambda: False):
    cfg=getattr(robot, "_omx_manager_snapshot", None)
    if cfg is None:
        cfg=managed_config()
        if cfg is None: return None
        require_home(cfg)
        from .config import digest
        robot._omx_manager_calibration_hash=digest(path_value(cfg["hardware"]["calibration_file"]))
        robot._omx_manager_snapshot=cfg
    else:
        from .config import digest
        if digest(path_value(cfg["hardware"]["calibration_file"])) != robot._omx_manager_calibration_hash:
            raise RuntimeError("Calibration changed during runtime; refusing another cycle")
    class Adapter:
        def read(self):
            heartbeat(ready=getattr(robot, "_omx_runtime_ready", False))
            return robot.get_observation()
        def send(self,values):
            robot._check_motor_health()
            robot.send_action({j+".pos":v for j,v in values.items()})
    # Home is an approach within a registered area, never a global collision-free planner.
    move_home(Adapter(),cfg,clock=time.monotonic,sleep=time.sleep,stopped=stopped)
    return {j+".pos":v for j,v in cfg["home"]["target"].items()}

class RolloutHome:
    def __init__(self,cfg,now,hold=None):
        self.cfg=cfg; self.detector=HomeDetector(cfg,now,hold=hold); self.next_report=0
    def reset(self,now): self.detector.reset(now)
    def update(self,observation,now):
        done=self.detector.update(observation,now)
        if now>=self.next_report:
            self.next_report=now+1
            rows={j:dict(row) for j,row in self.detector.rows.items()}
            try:
                write_json(ROOT/"state"/"manager"/self.cfg["device_id"]/"joint_errors.json",{"monotonic":now,"joints":rows,"stalled":self.detector.stalled},durable=False)
            except OSError as exc:
                if now>=getattr(self,"next_io_warning",0):
                    logger.warning("Joint diagnostics unavailable (control unchanged): %s",exc)
                    self.next_io_warning=now+10
        return done

_last_heartbeat=0.
def heartbeat(ready=True):
    global _last_heartbeat
    value=os.environ.get("OMX_MANAGER_HEARTBEAT")
    now=time.monotonic()
    if value and now-_last_heartbeat>=.5:
        write_json(value,{"pid":os.getpid(),"monotonic":now,"ready":ready},durable=False)
        _last_heartbeat=now
