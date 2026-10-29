"""Deterministic fake follower; no torch, motor SDK, camera, MQTT or file writes."""
import copy
from .config import JOINTS
from .home import HomeDetector,move_home

class Clock:
    def __init__(self): self.now=0.
    def __call__(self): return self.now
    def sleep(self,dt): self.now+=dt
class FakeFollower:
    def __init__(self,q=None): self.q=dict(q or {j:0. for j in JOINTS});self.sent=[];self.closed=False
    def read(self): return self.q.copy()
    def send(self,command): self.sent.append(command.copy());self.q=command.copy()

def exercise(cfg):
    cfg=copy.deepcopy(cfg);cfg["home"]["target"]={j:0. for j in JOINTS}
    cfg["counting"]["min_cycle_s"]=0
    clock=Clock();device=FakeFollower({j:2. for j in JOINTS})
    move_home(device,cfg,clock=clock,sleep=clock.sleep)
    counts=0;detector=HomeDetector(cfg)
    for _ in range(8):
        for q in (20.,20.,0.):
            clock.sleep(.05);detector.update({j:q for j in JOINTS},clock())
        for _ in range(35):
            clock.sleep(.05);counts+=detector.update({j:0. for j in JOINTS},clock())
    assert counts==8,counts
    return {"device":cfg["device_id"],"mode":"SIMULATION ONLY — YAML and motors unchanged","expected_cycles":8,"counted_cycles":counts,"home_commands":len(device.sent)}
