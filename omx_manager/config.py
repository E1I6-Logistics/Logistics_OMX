"""Strict YAML configuration. Importing this module never opens hardware."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import yaml
from .parameters import DIAGNOSTIC_DEFAULTS, SERVICE_DEFAULTS, annotated_yaml

ROOT = Path(__file__).resolve().parents[1]
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
IDS = {name: 11 + i for i, name in enumerate(JOINTS)}

class ConfigError(ValueError): pass

class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result: raise ConfigError(f"Duplicate YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result

def only_keys(value, allowed, label):
    extras = set(value) - set(allowed.split())
    if extras: raise ConfigError(f"Unknown {label} keys: {sorted(extras)}")

def number(value, label, lo=0, hi=float("inf")):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ConfigError(f"{label}: finite number in [{lo}, {hi}] required")
    return float(value)

def path_value(value, base=ROOT):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("A nonempty path is required; configure the device YAML first")
    path = Path(os.path.expandvars(value)).expanduser()
    return path if path.is_absolute() else Path(base) / path

def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): hasher.update(block)
    return hasher.hexdigest()

def atomic_write(path, data, *, durable=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data); stream.flush()
            if durable: os.fsync(stream.fileno())
        os.replace(name, path)
        if durable:
            directory = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)

def write_json(path, value, *, durable=True): atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", durable=durable)

def load(path):
    path = Path(path)
    with path.open(encoding="utf-8") as stream: cfg = yaml.load(stream, Loader=UniqueLoader)
    validate(cfg)
    return cfg

def diagnostic_options(cfg):
    return dict(DIAGNOSTIC_DEFAULTS, **cfg.get("diagnostics", {}))

def service_options(cfg):
    return dict(SERVICE_DEFAULTS, **cfg.get("service", {}))

def profile(cfg):
    models = cfg["models"]
    return models["profiles"][models["active_profile"]]

def validate(cfg):
    if not isinstance(cfg, dict) or cfg.get("schema_version") != 1: raise ConfigError("schema_version must be 1")
    if cfg.get("device_id") not in ("omx1", "omx2", "omx3", "omx4"): raise ConfigError("Unknown device_id")
    expected = "pickup" if cfg["device_id"] in ("omx1", "omx2") else "unload"
    if cfg.get("role") != expected: raise ConfigError("role does not match device_id")
    for section in ("hardware", "home", "models", "runtime", "recovery", "counting"):
        if not isinstance(cfg.get(section), dict): raise ConfigError(f"Missing section: {section}")
    only_keys(cfg, "schema_version device_id role hardware home models runtime recovery counting diagnostics service workflow unload view", "top-level")
    only_keys(cfg["hardware"], "port calibration_file robot_id disable_torque_on_disconnect cameras", "hardware")
    only_keys(cfg["home"], "target binding tolerance hold_s unload_hold_s still_delta max_sample_gap_s capture_s capture_max_span arrival_tolerance move_speed move_timeout_s max_start_error max_step max_following_error", "home")
    only_keys(cfg["models"], "active_profile profiles", "models")
    only_keys(cfg["runtime"], "broker_host broker_port cpu_threads fps cycle_timeout_s order_per_unit_s order_load_grace_s startup_timeout_s heartbeat_timeout_s shutdown_timeout_s camera_auto_reconnect camera_reconnect_attempts camera_reconnect_interval_s", "runtime")
    only_keys(cfg["recovery"], "retry_initial_s retry_max_s release_delay_s auto_home poll_s routes interrupted_order_action", "recovery")
    if cfg['recovery'].get('interrupted_order_action','fail_and_idle') not in ('fail_and_idle','require_ack'):
        raise ConfigError('Invalid recovery.interrupted_order_action')
    if type(cfg['runtime'].get('camera_auto_reconnect',True)) is not bool:raise ConfigError('camera_auto_reconnect must be boolean')
    attempts=cfg['runtime'].get('camera_reconnect_attempts',2)
    if type(attempts) is not int or not 1<=attempts<=3:raise ConfigError('camera_reconnect_attempts must be 1..3')
    number(cfg['runtime'].get('camera_reconnect_interval_s',1.),'camera_reconnect_interval_s',.2,5.)
    from .cycles import DEFAULTS as CYCLE_DEFAULTS
    only_keys(cfg["counting"], "leave_tolerance min_cycle_s stall_s progress_epsilon "+' '.join(CYCLE_DEFAULTS), "counting")
    hw = cfg["hardware"]
    for key in ("port", "calibration_file", "robot_id"): path_value(hw.get(key))
    if not isinstance(hw.get("disable_torque_on_disconnect"), bool): raise ConfigError("Explicit torque shutdown policy required")
    if not isinstance(hw.get("cameras"), dict) or not hw["cameras"]: raise ConfigError("Camera configuration required")
    for key, cam in hw["cameras"].items():
        if cam.get("type") not in ("opencv", "intelrealsense"): raise ConfigError(f"Unsupported camera: {key}")
        for dim in ("width", "height", "fps"): number(cam.get(dim), f"camera.{key}.{dim}", 1, 4096)
    home = cfg["home"]
    target = home.get("target")
    if target is not None:
        if not isinstance(target, dict) or set(target) != set(JOINTS): raise ConfigError("home.target must contain exactly six joints")
        for j in JOINTS: number(target[j], f"home.target.{j}", 0 if j == "gripper" else -100, 100)
    for key, low, high in (("hold_s", .1, 30), ("unload_hold_s", .1, 120), ("max_sample_gap_s", .03, 2), ("capture_s", .2, 60),
                           ("capture_max_span", .01, 10), ("arrival_tolerance", .01, 3), ("move_speed", .1, 20), ("move_timeout_s", 1, 180),
                           ("max_start_error", .1, 30), ("max_step", .01, 2)):
        number(home.get(key), f"home.{key}", low, high)
    number(home.get("max_following_error", 3.0), "home.max_following_error", .1, 30)
    if set(home.get("tolerance", {})) != set(JOINTS): raise ConfigError("Six joint tolerances required")
    for j, tol in home["tolerance"].items(): number(tol, f"tolerance.{j}", .1, 20)
    number(home.get("still_delta"), "home.still_delta", .001, 5)
    for key, lo, hi in (("leave_tolerance", 1, 100), ("min_cycle_s", 0, 120), ("stall_s", 1, 120), ("progress_epsilon", .01, 10)):
        number(cfg["counting"].get(key), "counting." + key, lo, hi)
    if cfg["counting"]["leave_tolerance"] <= max(home["tolerance"].values()): raise ConfigError("leave_tolerance must exceed all home tolerances")
    models = cfg["models"]
    if not isinstance(models.get("profiles"), dict) or models.get("active_profile") not in models["profiles"]:
        raise ConfigError("Unknown model profile")
    selected = profile(cfg)
    # Future artifacts may be registered, but never silently executed by the FP32 loader.
    if selected.get("runtime") != "lerobot_pytorch" or selected.get("precision") != "fp32":
        raise ConfigError("Only lerobot_pytorch / fp32 is implemented; quantized runtime requires separate validation")
    items = selected.get("items", {})
    allowed = {"omx1": {"A", "B"}, "omx2": {"C", "D"}, "omx3": {"E"}, "omx4": {"F"}}[cfg["device_id"]]
    if set(items) != allowed: raise ConfigError(f"Expected model items: {sorted(allowed)}")
    for item, entry in items.items():
        path_value(entry.get("path"))
        if not isinstance(entry.get("task"), str) or not entry["task"].strip(): raise ConfigError(f"Missing task: {item}")
    if not isinstance(cfg["runtime"].get("broker_host"), str) or not cfg["runtime"]["broker_host"].strip(): raise ConfigError("broker_host required")
    port = cfg["runtime"].get("broker_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535: raise ConfigError("Invalid broker_port")
    number(cfg["runtime"].get("fps"), "runtime.fps", 1, 60)
    threads = cfg["runtime"].get("cpu_threads")
    if isinstance(threads, bool) or not isinstance(threads, int) or not 1 <= threads <= 4: raise ConfigError("cpu_threads must be 1..4")
    for key in ("cycle_timeout_s", "order_per_unit_s", "order_load_grace_s", "startup_timeout_s", "heartbeat_timeout_s", "shutdown_timeout_s"):
        number(cfg["runtime"].get(key), key, 1, 3600)
    for key in ("retry_initial_s", "retry_max_s"): number(cfg["recovery"].get(key), key, .1, 300)
    if cfg["recovery"]["retry_initial_s"] > cfg["recovery"]["retry_max_s"]: raise ConfigError("Invalid retry backoff")
    if "diagnostics" in cfg and not isinstance(cfg["diagnostics"],dict): raise ConfigError("diagnostics must be a mapping")
    diag=diagnostic_options(cfg)
    only_keys(diag," ".join(DIAGNOSTIC_DEFAULTS),"diagnostics")
    for name in ("enabled","important_only"):
        if type(diag[name]) is not bool:raise ConfigError("diagnostics."+name+" must be true/false")
    for name,lo,hi in (("interval_s",1,30),("stale_s",2,120),("full_log_mb",.5,20),("important_log_mb",.5,10),("slow_warning_interval_s",1,120)):
        number(diag[name],"diagnostics."+name,lo,hi)
    for name,lo,hi in (("backups",1,5),("queue_lines",16,2048)):
        if type(diag[name]) is not int or not lo<=diag[name]<=hi:raise ConfigError("Invalid diagnostics."+name)
    if diag["stale_s"]<2*diag["interval_s"]:raise ConfigError("diagnostics.stale_s must be >=2*interval_s")
    if "service" in cfg and not isinstance(cfg["service"],dict):raise ConfigError("service must be a mapping")
    service=service_options(cfg);only_keys(service," ".join(SERVICE_DEFAULTS),"service")
    number(service["restart_s"],"service.restart_s",1,120)
    number(service["stop_timeout_s"],"service.stop_timeout_s",10,7200)
    if type(service["enable_on_boot"]) is not bool:raise ConfigError("service.enable_on_boot must be true/false")
    number(cfg["recovery"].get("release_delay_s", 10), "recovery.release_delay_s", 3, 60)
    from .workflow import validate_workflow
    from .basket import validate_unload
    from .return_path import validate_routes
    validate_workflow(cfg)
    validate_unload(cfg)
    from .view_options import validate as validate_view
    validate_view(cfg)
    validate_routes(cfg)
    from .cycles import validate_cycles
    validate_cycles(cfg)
    return cfg


def binding(cfg):
    artifacts = {}
    for item, entry in profile(cfg)["items"].items():
        folder = path_value(entry["path"])
        files = {"config.json", "model.safetensors", "policy_preprocessor.json", "policy_postprocessor.json"}
        for name in ("policy_preprocessor.json", "policy_postprocessor.json"):
            for step in json.loads((folder / name).read_text()).get("steps", []):
                if step.get("state_file"): files.add(step["state_file"])
        artifacts[item] = {name: digest(folder / name) for name in sorted(files)}
    material = {"artifacts": artifacts, "device": cfg["device_id"], "port": str(path_value(cfg["hardware"]["port"])),
                "robot_id": cfg["hardware"]["robot_id"], "calibration_sha256": digest(path_value(cfg["hardware"]["calibration_file"])),
                "profile": profile(cfg), "cameras": cfg["hardware"]["cameras"]}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

def require_home(cfg):
    if cfg["home"]["target"] is None: raise ConfigError("Home not registered. Run capture at the correct physical starting pose first")
    if cfg["home"].get("binding") != binding(cfg): raise ConfigError("Home binding changed (device/model/camera/calibration). Register or verify home again")

def save(path, cfg):
    validate(cfg)
    path = Path(path)
    if path.exists():
        backup = path.parent / "backups" / (path.name + "." + str(time.time_ns()) + ".yaml")
        atomic_write(backup, path.read_text(encoding="utf-8"))
    atomic_write(path, annotated_yaml(cfg))

def check_files(cfg, models=True):
    hw = cfg["hardware"]
    calibration = path_value(hw["calibration_file"])
    if calibration.stem != hw["robot_id"]: raise ConfigError("calibration filename stem must match hardware.robot_id")
    data = json.loads(calibration.read_text())
    if set(data) != set(JOINTS): raise ConfigError("Calibration must match the six OMX joints")
    for j in JOINTS:
        row = data[j]
        if row.get("id") != IDS[j] or row.get("drive_mode") not in (0, 1): raise ConfigError(f"Calibration identity mismatch: {j}")
        for name in ("range_min", "range_max", "homing_offset"):
            number(row.get(name), f"calibration.{j}.{name}", -2147483648, 2147483647)
        if row["range_min"] >= row["range_max"]: raise ConfigError(f"Invalid calibration range: {j}")
    if models:
        for item, entry in profile(cfg)["items"].items():
            folder = path_value(entry["path"])
            for file in ("config.json", "model.safetensors", "policy_preprocessor.json", "policy_postprocessor.json"):
                if not (folder / file).is_file(): raise ConfigError(f"{item}: missing {folder / file}")
            model = json.loads((folder / "config.json").read_text())
            if model.get("type") != "act" or model.get("output_features", {}).get("action", {}).get("shape") != [6]:
                raise ConfigError(f"{item}: expected six-joint ACT model")
            images = {key.removeprefix("observation.images."): value["shape"] for key, value in model.get("input_features", {}).items() if key.startswith("observation.images.")}
            if set(images) != set(hw["cameras"]): raise ConfigError(f"{item}: model camera keys {sorted(images)} do not match YAML")
            for key, shape in images.items():
                cam = hw["cameras"][key]
                if shape != [3, cam["height"], cam["width"]]: raise ConfigError(f"{item}: camera shape mismatch: {key}")
            for name in ("policy_preprocessor.json", "policy_postprocessor.json"):
                spec = json.loads((folder / name).read_text())
                for step in spec.get("steps", []):
                    if step.get("state_file") and not (folder / step["state_file"]).is_file(): raise ConfigError(f"{item}: missing processor state")
    return data
