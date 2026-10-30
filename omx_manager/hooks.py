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


def home_reporter(cfg):
    """One small snapshot per second, plus the final result; no image logging."""
    path = ROOT / "state" / "manager" / cfg["device_id"] / "home_motion.json"
    def report(data):
        write_json(path, dict(data, pid=os.getpid(), device=cfg["device_id"], at=time.time()), durable=False)
        phase = data["phase"]
        if phase in ("STARTING", "ARRIVED", "FAILED", "STOPPED"):
            message = f"[HOME] {phase} elapsed={data['elapsed_s']:.2f}s"
            if data.get("reason"): message += ": " + data["reason"]
            (logger.error if phase == "FAILED" else logger.info)(message)
    return report

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
            return robot.get_joint_observation()
        def send(self,values):
            robot._check_motor_health()
            robot.send_action({j+".pos":v for j,v in values.items()})
    # Home is an approach within a registered area, never a global collision-free planner.
    move_home(Adapter(),cfg,clock=time.monotonic,sleep=time.sleep,stopped=stopped,report=home_reporter(cfg))
    return {j+".pos":v for j,v in cfg["home"]["target"].items()}

class RolloutHome:
    def __init__(self,cfg,now,hold=None):
        self.cfg=cfg; self.hold=hold; self.next_report=0
        self.item=next(iter(cfg['models']['profiles'][cfg['models']['active_profile']]['items']))
        self.reset(now)
    def reset(self,now):
        from .cycles import options,CycleDetector
        if options(self.cfg)['mode']=='cycle':
            self.detector=CycleDetector(self.cfg,self.item,now);self.tracker=None;return
        from .workflow import StageTracker, require_stages
        self.detector=HomeDetector(self.cfg,now,hold=self.hold)
        self.tracker=StageTracker(require_stages(self.cfg,self.item),self.cfg['home']['max_sample_gap_s'])
    def select_item(self,item,now):
        # Pickup IPC uses A/B internally for both OMX1 and OMX2.
        self.item=({'A':'C','B':'D'}.get(item,item) if self.cfg['device_id']=='omx2' else item)
        self.reset(now)
    def invalidate(self,now):
        self.detector.invalidate(now)
        if self.tracker:self.tracker.reset()
    def note_action(self):
        if hasattr(self.detector,'note_action'):self.detector.note_action()
    def update(self,observation,now):
        if self.tracker:
            eligible=self.tracker.update(observation,now)
            done=self.detector.update(observation,now,eligible=eligible)
        else:done=self.detector.update(observation,now)
        if done or now>=self.next_report:
            self.next_report=now+1
            rows={j:dict(row) for j,row in self.detector.rows.items()}
            if done:
                from .home import joints
                q = joints(observation)
                rows = {j: {"target": self.detector.target[j], "current": q[j],
                            "error": abs(q[j] - self.detector.target[j]),
                            "tolerance": getattr(self.detector,'profile',{}).get('tolerance',self.cfg['home']['tolerance']).get(j,self.cfg['home']['tolerance'][j]),
                            "ignored": self.tracker is None and j=='gripper',
                            "ok": (self.tracker is None and j=='gripper') or abs(q[j] - self.detector.target[j]) <= getattr(self.detector,'profile',{}).get('tolerance',self.cfg['home']['tolerance']).get(j,self.cfg['home']['tolerance'][j])}
                        for j in q}
            try:
                state=self.detector.last_completion if done else dict(departed=self.detector.departed,in_home_window=self.detector.in_home_window,
                        still=self.detector.still,hold_since=self.detector.since,sample_gap_s=self.detector.last_gap_s,gap_reset=self.detector.gap_reset)
                row={"monotonic":now,"at":time.time(),"pid":os.getpid(),"session":os.environ.get('OMX_SESSION'),"joints":rows,"stalled":self.detector.stalled,
                        "completed":done,"departed":self.detector.departed,"in_home_window":self.detector.in_home_window,
                        "still":self.detector.still,"hold_since":self.detector.since,
                        "sample_gap_s":self.detector.last_gap_s,"gap_reset":self.detector.gap_reset,
                        "item":self.item,"stage_index":self.tracker.index if self.tracker else None,"stage_total":len(self.tracker.stages) if self.tracker else None,
                        "counter_mode":"ordered_stages" if self.tracker else "cycle",
                        "actions":getattr(self.detector,'actions',None),"min_actions":self.cfg['counting'].get('min_actions',30),
                        "phase":getattr(self.detector,'phase','LEGACY'),"peak_excursion":getattr(self.detector,'peak',None),
                        "physical_transfer_verified":False,**state}
                write_json(ROOT/"state"/"manager"/self.cfg["device_id"]/"joint_errors.json",row,durable=False)
                if done: write_json(ROOT/'state'/'manager'/self.cfg['device_id']/'last_cycle.json',row,durable=False)
            except OSError as exc:
                if now>=getattr(self,"next_io_warning",0):
                    logger.warning("Joint diagnostics unavailable (control unchanged): %s",exc)
                    self.next_io_warning=now+10
        if done and self.tracker:self.tracker.reset()
        return done


def note_action(robot):
    counter=getattr(robot,'_omx_cycle_counter',None)
    if isinstance(counter,RolloutHome):counter.note_action()


def recovery_adapter(robot):
    """Fresh joint feedback only. Never enable torque or rewrite motor modes."""
    class Adapter:
        first=True
        def read(self):
            heartbeat(ready=False)
            if self.first: robot._omx_last_health=-1e9
            robot._check_motor_health(require_torque=True)
            if self.first:
                for joint in robot.bus.motors:
                    expected=5 if joint=='gripper' else 4
                    if robot.bus.read('Operating_Mode',joint)!=expected:
                        raise RuntimeError('Motor mode changed; no automatic recovery')
                self.first=False
            return robot.get_joint_observation()
        def send(self,values):
            if any(robot.bus.read('Torque_Enable',j)!=1 for j in values):
                raise RuntimeError('Torque OFF; no automatic recovery')
            robot.send_action({j+'.pos':v for j,v in values.items()})
    return Adapter()


def recover_after_fault(robot, stopped=lambda:False):
    """At most one bounded recovery attempt; the failed job is never replayed."""
    from .return_path import return_home
    cfg=getattr(robot,'_omx_manager_snapshot',None) or managed_config()
    if cfg is None or stopped(): return False
    path=ROOT/'state'/'manager'/cfg['device_id']/'recovery_motion.json'
    try:
        require_home(cfg)
        if not cfg['recovery'].get('auto_home',True): raise RuntimeError('Automatic Home recovery disabled')
        result=return_home(recovery_adapter(robot),cfg,clock=time.monotonic,sleep=time.sleep,stopped=stopped,report=home_reporter(cfg),preserve_gripper=True)
        full_home=abs(result['gripper']-cfg['home']['target']['gripper'])<=cfg['home']['arrival_tolerance']
        write_json(path,dict(at=time.time(),phase='HOME_RECOVERED' if full_home else 'ARM_HOME_RECOVERED',pid=os.getpid(),job_replayed=False,gripper_preserved=True))
        logger.warning('[RECOVERY] Arm returned; gripper preserved; full_home=%s; failed job remains failed',full_home)
        return True
    except Exception as exc:
        write_json(path,dict(at=time.time(),phase='RECOVERY_WAIT',pid=os.getpid(),reason=str(exc),job_replayed=False))
        logger.error('[RECOVERY] RECOVERY_WAIT: %s',exc)
        return False

_last_heartbeat=0.
def heartbeat(ready=True):
    global _last_heartbeat
    value=os.environ.get("OMX_MANAGER_HEARTBEAT")
    now=time.monotonic()
    if value and now-_last_heartbeat>=.5:
        write_json(value,{"pid":os.getpid(),"monotonic":now,"ready":ready},durable=False)
        _last_heartbeat=now
