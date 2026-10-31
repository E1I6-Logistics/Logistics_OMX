"""Observed work stages; a motion cycle is never proof of object transfer."""
import hashlib
import json

from .config import ConfigError, JOINTS, number, only_keys, profile
from .home import joints


def registration_binding(cfg):
    material = {"home_binding": cfg["home"]["binding"], "target": cfg["home"]["target"]}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def validate_workflow(cfg):
    spec = cfg.get("workflow", {"items": {}})
    if not isinstance(spec, dict): raise ConfigError("workflow must be a mapping")
    only_keys(spec, "items", "workflow")
    items = spec.get("items", {})
    if not isinstance(items, dict) or set(items) - set(profile(cfg)["items"]):
        raise ConfigError("workflow.items must use this device's external item names")
    for item, row in items.items():
        if not isinstance(row, dict): raise ConfigError("Invalid workflow item: " + item)
        only_keys(row, "binding stages", "workflow item")
        stages = row.get("stages", [])
        if not isinstance(stages, list) or len(stages) > 12: raise ConfigError("At most 12 work stages")
        names = set()
        for stage in stages:
            if not isinstance(stage, dict): raise ConfigError("Invalid stage")
            only_keys(stage, "name target tolerance samples", "stage")
            name = stage.get("name")
            if not isinstance(name, str) or not name or name in names: raise ConfigError("Unique stage names required")
            names.add(name)
            joints(stage.get("target", {}))
            if set(stage["target"]) != set(JOINTS): raise ConfigError("Stage target needs exactly six joints")
            number(stage.get("tolerance", 5.0), "stage.tolerance", .1, 20)
            if type(stage.get("samples", 2)) is not int or not 2 <= stage.get("samples", 2) <= 30:
                raise ConfigError("stage.samples must be 2..30")


def require_stages(cfg, item):
    row = cfg.get("workflow", {}).get("items", {}).get(item, {})
    stages = row.get("stages", [])
    if row.get("binding") != registration_binding(cfg) or len(stages) < 2:
        raise ConfigError(f"{item}: register at least two ordered work stages (stage-capture); Home alone cannot count")
    # A gripper twitch or a pose in the Home counting window cannot arm completion.
    for stage in stages:
        tol = stage.get("tolerance", 5.0)
        if not any(abs(stage["target"][j] - cfg["home"]["target"][j]) > cfg["home"]["tolerance"][j] + tol
                   for j in JOINTS if j != "gripper"):
            raise ConfigError(f"{item}/{stage['name']}: work stage overlaps Home; register an actual work pose")
    for before, after in zip(stages, stages[1:]):
        if not any(abs(before["target"][j] - after["target"][j]) > before.get("tolerance", 5.) + after.get("tolerance", 5.)
                   for j in JOINTS if j != "gripper"):
            raise ConfigError(f"{item}: consecutive work regions overlap; tighten tolerances or register distinct poses")
    return stages


class StageTracker:
    def __init__(self, stages, max_gap):
        self.stages = stages
        self.max_gap = max_gap
        self.reset()

    def reset(self):
        self.index = 0
        self.samples = 0
        self.last = None

    @property
    def complete(self): return self.index == len(self.stages) and bool(self.stages)

    def update(self, observation, now):
        q = joints(observation)
        number(now, "stage timestamp", 0)
        if self.last is None or not 0 < now - self.last <= self.max_gap:
            self.samples = 0
        self.last = now
        if self.complete: return True
        if not self.stages: return False
        stage = self.stages[self.index]
        inside = all(abs(q[j] - stage["target"][j]) <= stage.get("tolerance", 5.) for j in JOINTS)
        self.samples = self.samples + 1 if inside else 0
        if self.samples >= stage.get("samples", 2):
            self.index += 1
            self.samples = 0
        return self.complete
