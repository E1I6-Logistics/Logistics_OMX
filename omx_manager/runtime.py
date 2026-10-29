"""Build direct LeRobot arguments from YAML; supervise the whole process tree."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
from .config import ROOT, JOINTS, path_value, check_files, require_home, profile, write_json, load
from .hardware import Lock

class OperatorRecoveryRequired(RuntimeError): pass

def state_dir(cfg): return ROOT / "state" / "manager" / cfg["device_id"]
def device_lock(cfg): return Lock(state_dir(cfg) / "device.lock")

def initialize_inventory(path, capacity=8):
    path = Path(path)
    if path.exists():
        data = json.loads(path.read_text())
        remaining=data.get("remaining"); total=data.get("total_completed")
        if (data.get("capacity") != capacity or type(remaining) is not int or type(total) is not int
                or not 0 <= total <= capacity or remaining != capacity - total):
            raise OperatorRecoveryRequired("Invalid inventory state; inspect locally")
        if data["remaining"] > 0:
            raise OperatorRecoveryRequired("Unfinished physical order preserved. Inspect cargo and use acknowledge-recovery; no replay")
        return
    write_json(path, {"capacity":capacity,"total_completed":capacity,"remaining":0,"order_item":"WAIT", "order_quantity":0,"order_completed":0,"order_started_at_epoch":time.time()})

def acknowledge_recovery(cfg):
    directory = state_dir(cfg)
    for name in ("inventory.json", "jobs.json", "workstation_command.json", "workstation_status.json"):
        path = directory / name
        if path.exists():
            destination = directory / "archive" / (str(time.time_ns()) + "_" + name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Archive is never discarded, including completed-job replay records.
            if name == "jobs.json":
                data=json.loads(path.read_text())
                for row in data.values():
                    if row.get("state") == "RUNNING": row.update(state="INTERRUPTED", success=False, message="Operator reconciled physical order")
                write_json(path, data)
            else: path.replace(destination)

def build(cfg, config_path, python=sys.executable, session=None):
    directory = state_dir(cfg); session = session or uuid.uuid4().hex
    env = os.environ.copy()
    env.update(PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / "lerobot_unified/src") + os.pathsep + env.get("PYTHONPATH", ""),
        OMX_MANAGER_CONFIG=str(Path(config_path).resolve()), OMX_MANAGER_HEARTBEAT=str(directory / "heartbeat.json"),
        OMX_INVENTORY_STATE_PATH=str(directory / "inventory.json"), OMX_ABORT_FILE=str(directory / "abort"),
        OMX_JOB_JOURNAL=str(directory / "jobs.json"), OMX_BROKER_PORT=str(cfg["runtime"]["broker_port"]),
        OMX_PICKUP_PER_UNIT_S=str(cfg["runtime"]["order_per_unit_s"]), OMX_PICKUP_LOAD_GRACE_S=str(cfg["runtime"]["order_load_grace_s"]),
        OMX_ACT_PREFETCH="0")
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = str(cfg["runtime"]["cpu_threads"])
    hw=cfg["hardware"]; home=cfg["home"]; timing=cfg["runtime"]
    items=profile(cfg)["items"]
    commands=[python,"-m","lerobot.scripts.lerobot_rollout","--strategy.type=base","--device=cpu", "--display_data=false", "--duration=0", "--return_to_initial_position=false", "--interpolation_multiplier=1",
        "--fps="+str(timing["fps"]),"--robot.type=omx_follower","--robot.port="+str(path_value(hw["port"])),
        "--robot.id="+hw["robot_id"],"--robot.calibration_dir="+str(path_value(hw["calibration_file"]).parent),
        "--robot.disable_torque_on_disconnect="+str(hw["disable_torque_on_disconnect"]).lower(),
        "--robot.cameras="+json.dumps(hw["cameras"]),
        "--strategy.home_tolerance="+str(max(home["tolerance"].values())), "--strategy.home_leave_tolerance="+str(cfg["counting"]["leave_tolerance"]),
        "--strategy.home_hold_s="+str(home["hold_s"]), "--strategy.min_cycle_s="+str(cfg["counting"]["min_cycle_s"])]
    external_order = {"omx1": ("A", "B"), "omx2": ("C", "D"), "omx3": ("E",), "omx4": ("F",)}[cfg["device_id"]]
    first=items[external_order[0]]
    commands.extend(["--policy.path="+str(path_value(first["path"])), "--task="+first["task"]])
    generated={}
    if cfg["role"]=="pickup":
        mapping={local:dict(items[external],path=str(path_value(items[external]["path"]))) for local,external in zip(("A","B"),external_order)}
        generated["order_models.json"]=mapping
        commands.extend(["--strategy.auto_restart_on_home=true", "--strategy.keep_alive_when_inventory_empty=true",
            "--strategy.order_models_file="+str(directory/"order_models.json"), "--strategy.inventory_state_path="+str(directory/"inventory.json"),
            "--strategy.pickup_cycle_timeout_s="+str(timing["cycle_timeout_s"]), "--strategy.pickup_home_stall_s=0"])
    else:
        env.update(OMX_WORKSTATION_MODE="true", OMX_WORKSTATION_SESSION=session,
            OMX_WORKSTATION_STATUS_FILE=str(directory/"workstation_status.json"), OMX_WORKSTATION_CONTROL_FILE=str(directory/"workstation_command.json"))
        generated["workstation_status.json"]={"session":session,"phase":"BOOTING","pid":None}
        # Never consume a stale command from a previous launcher instance.
        generated["workstation_command.json"]={"session":session,"command":"WAIT"}
        commands.extend(["--strategy.auto_restart_on_home=false", "--strategy.workstation_wait_enabled=true", "--strategy.idle_complete_enabled=true", "--strategy.idle_complete_hold_s="+str(home["unload_hold_s"]),
            "--strategy.workstation_control_file="+env["OMX_WORKSTATION_CONTROL_FILE"], "--strategy.workstation_status_file="+env["OMX_WORKSTATION_STATUS_FILE"],
            "--strategy.workstation_job_timeout_s="+str(timing["cycle_timeout_s"])])
    mqtt=[python,str(ROOT/"lerobot_unified/omx_mqtt_client.py"),"--robot-id",cfg["device_id"],"--broker-ip",timing["broker_host"]]
    return commands,mqtt,env,generated

def stop_process(proc, timeout):
    if proc is None: return
    # Group termination also reclaims children of a dead leader.
    try: os.killpg(proc.pid,signal.SIGTERM)
    except ProcessLookupError: pass
    try: proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait(timeout=3)
    # A leader can exit before its children; terminate any remaining descendants.
    try: os.killpg(proc.pid,signal.SIGKILL)
    except ProcessLookupError: pass

def healthy_heartbeat(path, pid, now, timeout):
    try:
        data=json.loads(Path(path).read_text())
        return data["pid"]==pid and 0 <= now-data["monotonic"] <= timeout
    except (OSError,ValueError,KeyError,TypeError): return False

class Supervisor:
    def __init__(self, config_path): self.path=Path(config_path); self.stopped=threading.Event()
    def stop(self, *_): self.stopped.set()
    def run_once(self,cfg):
        check_files(cfg); require_home(cfg)
        directory=state_dir(cfg); directory.mkdir(parents=True,exist_ok=True)
        journal=directory/"jobs.json"
        if journal.exists() and any(row.get("state")=="RUNNING" for row in json.loads(journal.read_text()).values()):
            raise OperatorRecoveryRequired("Interrupted physical job; reconcile before restart. Journal preserved")
        if cfg["role"]=="pickup": initialize_inventory(directory/"inventory.json")
        for name in ("abort","heartbeat.json"):
            (directory/name).unlink(missing_ok=True)
        rollout,mqtt,env,generated=build(cfg,self.path)
        for name,data in generated.items(): write_json(directory/name,data)
        worker=client=None
        try:
            worker=subprocess.Popen(rollout,env=env,cwd=ROOT/"lerobot_unified",start_new_session=True)
            env["OMX_ROLLOUT_PID"]=str(worker.pid)
            deadline=time.monotonic()+cfg["runtime"]["startup_timeout_s"]
            while not self.stopped.is_set():
                if worker.poll() is not None: raise RuntimeError(f"Rollout startup exit={worker.returncode}")
                if healthy_heartbeat(directory/"heartbeat.json",worker.pid,time.monotonic(),cfg["runtime"]["heartbeat_timeout_s"]):
                    data=json.loads((directory/"heartbeat.json").read_text())
                    ready = data.get("ready")
                    if cfg["role"] == "unload":
                        try:
                            status=json.loads((directory/"workstation_status.json").read_text())
                            ready=ready and status.get("phase")=="READY" and status.get("session")==env["OMX_WORKSTATION_SESSION"] and status.get("pid")==worker.pid
                        except (OSError,ValueError): ready=False
                    if ready: break
                if time.monotonic()>deadline: raise TimeoutError("Rollout not READY in time")
                self.stopped.wait(.2)
            if self.stopped.is_set(): return
            client=subprocess.Popen(mqtt,env=env,cwd=ROOT/"lerobot_unified",start_new_session=True)
            while not self.stopped.wait(.25):
                if worker.poll() is not None: raise RuntimeError(f"Rollout exit={worker.returncode}")
                if client.poll() is not None: raise RuntimeError(f"MQTT exit={client.returncode}")
                if not healthy_heartbeat(directory/"heartbeat.json",worker.pid,time.monotonic(),cfg["runtime"]["heartbeat_timeout_s"]): raise TimeoutError("Rollout heartbeat stalled")
        finally:
            write_json(directory/"last_stop.json",{"at":time.time(),"operator_stop":self.stopped.is_set()})
            for proc in (client,worker):
                try: stop_process(proc,cfg["runtime"]["shutdown_timeout_s"])
                except Exception as exc: print("Cleanup failure:",exc,flush=True)
    def run(self):
        cfg=load(self.path)
        with device_lock(cfg):
            delay=cfg["recovery"]["retry_initial_s"]
            old={s:signal.signal(s,self.stop) for s in (signal.SIGINT,signal.SIGTERM)}
            try:
                while not self.stopped.is_set():
                    try:
                        cfg=load(self.path)
                        self.run_once(cfg)
                        delay=cfg["recovery"]["retry_initial_s"]
                    except Exception as exc:
                        print(f"[RECOVERY] {type(exc).__name__}: {exc}; retry in {delay}s",flush=True)
                        write_json(state_dir(cfg)/"recovery.json",{"at":time.time(),"error":str(exc),"retry_s":delay})
                    if self.stopped.wait(delay): break
                    delay=min(delay*2,cfg["recovery"]["retry_max_s"])
            finally:
                for s,handler in old.items(): signal.signal(s,handler)
