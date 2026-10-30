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
from .config import ROOT, JOINTS, path_value, check_files, require_home, profile, write_json, load, diagnostic_options
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
    for name in ("inventory.json", "jobs.json", "workstation_command.json", "workstation_status.json", "unload_progress.json"):
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

def settle_interrupted_pickup(cfg):
    """Fail old orders without replay; retain IDs, progress and an audit copy.

    Reaching a known pose is still enforced separately by preflight. This does
    not assert that cargo was transferred or authorize a gripper release.
    """
    if cfg['role']!='pickup' or cfg['recovery'].get('interrupted_order_action','fail_and_idle')!='fail_and_idle':return False
    directory=state_dir(cfg);path=directory/'inventory.json';journal=directory/'jobs.json'
    jobs=json.loads(journal.read_text()) if journal.exists() else {}
    if not isinstance(jobs,dict):raise OperatorRecoveryRequired('Invalid job journal; inspect locally')
    inventory=json.loads(path.read_text()) if path.exists() else None
    if inventory is not None:
        if (not isinstance(inventory,dict) or inventory.get('capacity')!=8 or type(inventory.get('remaining')) is not int
                or type(inventory.get('total_completed')) is not int or not 0<=inventory['remaining']<=8
                or inventory['remaining']!=8-inventory['total_completed']):
            raise OperatorRecoveryRequired('Invalid inventory state; inspect locally')
    if any(not isinstance(row,dict) for row in jobs.values()):raise OperatorRecoveryRequired('Invalid job journal; inspect locally')
    running=[key for key,row in jobs.items() if row.get('state')=='RUNNING']
    if not running and not (inventory and inventory['remaining']):return False
    at=time.time();archive=directory/'archive'/f'{time.time_ns()}_interrupted_pickup.json'
    write_json(archive,dict(at=at,device=cfg['device_id'],inventory=inventory,jobs=jobs,
                          physical_transfer_verified=False,job_replayed=False))
    for key in running:
        jobs[key].update(state='INTERRUPTED',success=False,message='Interrupted order failed on restart; no replay; cargo unverified',finished_at=at)
    if running:write_json(journal,jobs)
    # Do not infer remaining shelf stock. WAIT means no active order, as at boot.
    if inventory:
        path.unlink()
        initialize_inventory(path)
    write_json(directory/'interrupted_order.json',dict(at=at,job_ids=running,archive=str(archive),
               action='FAILED_NO_REPLAY',physical_transfer_verified=False))
    return True

def build(cfg, config_path, python=sys.executable, session=None, calibration_item=None):
    directory = state_dir(cfg); session = session or uuid.uuid4().hex
    env = os.environ.copy()
    env.pop("OMX_UNLOAD_VIEW_SCOPE",None)
    for key in ('OMX_CYCLE_CALIBRATION','OMX_CALIBRATION_ITEM','OMX_CALIBRATION_COMMAND','OMX_CALIBRATION_STATUS',
                'OMX_CALIBRATION_OUTPUT','OMX_CALIBRATION_PARENT_PID','OMX_CALIBRATION_PARENT_START'):
        env.pop(key,None)
    env.update(PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / "lerobot_unified/src") + os.pathsep + env.get("PYTHONPATH", ""),
        OMX_MANAGER_CONFIG=str(Path(config_path).resolve()), OMX_MANAGER_HEARTBEAT=str(directory / "heartbeat.json"),
        OMX_INVENTORY_STATE_PATH=str(directory / "inventory.json"), OMX_ABORT_FILE=str(directory / "abort"),
        OMX_JOB_JOURNAL=str(directory / "jobs.json"), OMX_BROKER_PORT=str(cfg["runtime"]["broker_port"]),
        OMX_PICKUP_PER_UNIT_S=str(cfg["runtime"]["order_per_unit_s"]), OMX_PICKUP_LOAD_GRACE_S=str(cfg["runtime"]["order_load_grace_s"]),
        OMX_ACT_PREFETCH="0",OMX_ACT_FAST_QUEUE='1',OMX_SESSION=session)
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
    commands.extend(['--strategy.camera_auto_reconnect='+str(timing.get('camera_auto_reconnect',True)).lower(),
                     '--strategy.camera_reconnect_attempts='+str(timing.get('camera_reconnect_attempts',2)),
                     '--strategy.camera_reconnect_interval_s='+str(timing.get('camera_reconnect_interval_s',1.)),
                     '--strategy.camera_status_path='+str(directory/'camera_status.json')])
    first=items[calibration_item or external_order[0]]
    commands.extend(["--policy.path="+str(path_value(first["path"])), "--task="+first["task"]])
    generated={}
    if calibration_item:
        env.update(OMX_CYCLE_CALIBRATION='1',OMX_CALIBRATION_ITEM=calibration_item)
        commands.extend(['--strategy.auto_restart_on_home=false','--strategy.idle_complete_enabled=false','--strategy.workstation_wait_enabled=false'])
    elif cfg["role"]=="pickup":
        mapping={local:dict(items[external],path=str(path_value(items[external]["path"]))) for local,external in zip(("A","B"),external_order)}
        generated["order_models.json"]=mapping
        commands.extend(["--strategy.auto_restart_on_home=true", "--strategy.keep_alive_when_inventory_empty=true",
            "--strategy.order_models_file="+str(directory/"order_models.json"), "--strategy.inventory_state_path="+str(directory/"inventory.json"),
            "--strategy.pickup_cycle_timeout_s="+str(timing["cycle_timeout_s"]), "--strategy.pickup_home_stall_s=0"])
    else:
        env.update(OMX_WORKSTATION_MODE="true", OMX_WORKSTATION_SESSION=session,OMX_MANAGED_UNLOAD='1',
            OMX_WORKSTATION_STATUS_FILE=str(directory/"workstation_status.json"), OMX_WORKSTATION_CONTROL_FILE=str(directory/"workstation_command.json"))
        from .basket import options as unload_options
        env['OMX_UNLOAD_VIEW_SCOPE']='visible_only' if unload_options(cfg)['registration_allow_partial'] else 'full'
        env['OMX_UNLOAD_TIMEOUT_S']=str(unload_options(cfg)['job_timeout_s']+timing['startup_timeout_s'])
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
    def __init__(self, config_path, diagnostics=None, important_only=None):
        self.path=Path(config_path); self.stopped=threading.Event()
        self.diagnostics=diagnostics; self.important_only=important_only; self.capture=None
    def message(self, line):
        if self.capture: self.capture.emit("manager",line)
        else: print(line,flush=True)
    def stop(self, *_): self.stopped.set()
    def preflight(self,cfg,allow_motion=True,verify_registration=True,operator_confirmed=False):
        """Motor-only checks before loading Torch/cameras; no order replay."""
        from .exposure import require_restored
        require_restored(cfg)
        from .commissioning import require_commissioned
        from .hardware import FollowerBus
        from .home import errors
        from .return_path import return_home
        from .hooks import home_reporter
        if verify_registration:
            check_files(cfg);require_commissioned(cfg)
        directory=state_dir(cfg)
        if settle_interrupted_pickup(cfg):
            self.message('[RECOVERY] Interrupted pickup archived as failed; no replay. Checking Home before accepting NEW orders')
        journal=directory/'jobs.json'
        if journal.exists() and any(row.get('state')=='RUNNING' for row in json.loads(journal.read_text()).values()):
            raise OperatorRecoveryRequired('Interrupted job; inspect cargo then request resume; no replay')
        if cfg['role']=='pickup': initialize_inventory(directory/'inventory.json')
        with FollowerBus(cfg) as device:
            q=device.read()
            for joint in JOINTS:
                if device.bus.read('Operating_Mode',joint)!=(5 if joint=='gripper' else 4):
                    raise OperatorRecoveryRequired('Motor mode mismatch; no automatic rewrite')
                if device.bus.read('Torque_Enable',joint)!=1:
                    raise OperatorRecoveryRequired('Torque OFF: support arm and use local arm command; no automatic enable')
            if max(errors(q,cfg['home']['target']).values())<=cfg['home']['arrival_tolerance']: return
            if not allow_motion or not cfg['recovery'].get('auto_home',True):
                raise OperatorRecoveryRequired('Position arm at registered Home or explicitly request another return-path check')
            result=return_home(device,cfg,clock=time.monotonic,sleep=time.sleep,stopped=self.stopped.is_set,report=home_reporter(cfg),preserve_gripper=not operator_confirmed)
            if abs(result['gripper']-cfg['home']['target']['gripper'])>cfg['home']['arrival_tolerance']:
                raise OperatorRecoveryRequired('Arm Home recovered with gripper held; inspect cargo then request resume')
    def wait_for_recovery(self,cfg,error):
        """Keep MQTT/diagnostics alive, never reload the model in this state."""
        from .config import digest
        directory=state_dir(cfg);directory.mkdir(parents=True,exist_ok=True)
        started=time.time();reason=str(error);client=None;signature=digest(self.path);registered=False;registration_checked=False
        # Configuration/position changes can be detected without reloading a model.
        # Generic runtime/USB faults require an explicit retry unless Home recovery succeeded.
        from .config import ConfigError
        allow_auto=isinstance(error,(ConfigError,OperatorRecoveryRequired))
        try:
            motion=json.loads((directory/'recovery_motion.json').read_text())
            allow_auto=allow_auto or (motion.get('phase')=='HOME_RECOVERED' and motion.get('at',0)>=getattr(self,'last_run_started',started))
        except (OSError,ValueError): pass
        try:
            _,mqtt,env,_=build(cfg,self.path)
            command=[mqtt[0],'-m','omx_manager.recovery_wait','--config',str(self.path.resolve())]
            def start_client():
                try:
                    proc=subprocess.Popen(command,env=env,cwd=ROOT,start_new_session=True,
                        **(dict(stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1) if self.capture else {}))
                    if self.capture: self.capture.attach(proc,'recovery-mqtt')
                    return proc
                except OSError as failure:
                    self.message('[RECOVERY] MQTT helper unavailable: '+str(failure))
                    return None
            client=start_client()
            self.message('[RECOVERY] RECOVERY_WAIT: '+reason+'; model not loaded; MQTT rejects new jobs')
            while not self.stopped.is_set():
                try:
                    write_json(directory/'recovery_wait.json',dict(phase='RECOVERY_WAIT',reason=reason,at=time.time(),
                               pid=os.getpid(),model_loaded=False,mqtt_alive=client is not None and client.poll() is None),durable=False)
                except OSError as failure: self.message('[RECOVERY] Status storage unavailable: '+str(failure))
                if self.stopped.wait(cfg['recovery'].get('poll_s',5.)): return
                if client is None or client.poll() is not None: client=start_client()
                request=directory/'resume_request.json'
                resume=False
                try:
                    if request.exists():
                        data=json.loads(request.read_text());request.unlink(missing_ok=True)
                        resume=(isinstance(data,dict) and isinstance(data.get('at'),(int,float)) and data['at']>=started and data.get('config')==str(self.path.resolve()))
                    current=digest(self.path)
                    if current!=signature: candidate=load(self.path)
                except (OSError,ValueError,TypeError) as failure:
                    reason='Recovery configuration/request invalid: '+str(failure);continue
                changed=current!=signature
                if changed:
                    cfg=candidate;signature=current;registered=False;registration_checked=False
                if resume:
                    try: acknowledge_recovery(cfg)
                    except (OSError,ValueError,TypeError) as failure:
                        reason='Cannot reconcile journal: '+str(failure);continue
                    registered=False;registration_checked=False
                try:
                    if not registration_checked:
                        registration_checked=True
                        from .commissioning import require_commissioned
                        check_files(cfg);require_commissioned(cfg);registered=True
                    if not registered: continue
                    if not (allow_auto or resume or changed): continue
                    self.preflight(cfg,allow_motion=resume,verify_registration=False,operator_confirmed=resume)
                    write_json(directory/'recovery_wait.json',dict(phase='RECHECKED',at=time.time(),pid=os.getpid(),model_loaded=False))
                    self.message('[RECOVERY] Home and registration rechecked; starting a fresh idle session, no job replay')
                    return
                except Exception as exc:
                    new_reason=str(exc)
                    if new_reason!=reason: self.message('[RECOVERY] RECOVERY_WAIT: '+new_reason)
                    reason=new_reason
        finally:
            if client is not None: stop_process(client,cfg['runtime']['shutdown_timeout_s'])
    def run_once(self,cfg):
        check_files(cfg); require_home(cfg)
        self.last_run_started=time.time()
        directory=state_dir(cfg); directory.mkdir(parents=True,exist_ok=True)
        journal=directory/"jobs.json"
        if journal.exists() and any(row.get("state")=="RUNNING" for row in json.loads(journal.read_text()).values()):
            raise OperatorRecoveryRequired("Interrupted physical job; reconcile before restart. Journal preserved")
        if cfg["role"]=="pickup": initialize_inventory(directory/"inventory.json")
        for name in ("abort","heartbeat.json","joint_errors.json","metrics.json"):
            (directory/name).unlink(missing_ok=True)
        rollout,mqtt,env,generated=build(cfg,self.path)
        env.pop("OMX_METRICS_PATH", None)
        if self.diagnostics:
            env["OMX_METRICS_PATH"]=str(directory/"metrics.json")
            env["OMX_METRICS_INTERVAL_S"]=str(diagnostic_options(cfg)["interval_s"])
        capture_options = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace", bufsize=1) if self.capture else {}
        for name,data in generated.items(): write_json(directory/name,data)
        worker=client=None
        try:
            worker=subprocess.Popen(rollout,env=env,cwd=ROOT/"lerobot_unified",start_new_session=True,**capture_options)
            if self.capture: self.capture.attach(worker,"rollout")
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
            client=subprocess.Popen(mqtt,env=env,cwd=ROOT/"lerobot_unified",start_new_session=True,**capture_options)
            if self.capture: self.capture.attach(client,"mqtt")
            while not self.stopped.wait(.25):
                if worker.poll() is not None: raise RuntimeError(f"Rollout exit={worker.returncode}")
                if client.poll() is not None: raise RuntimeError(f"MQTT exit={client.returncode}")
                if not healthy_heartbeat(directory/"heartbeat.json",worker.pid,time.monotonic(),cfg["runtime"]["heartbeat_timeout_s"]): raise TimeoutError("Rollout heartbeat stalled")
        finally:
            try: write_json(directory/"last_stop.json",{"at":time.time(),"operator_stop":self.stopped.is_set()})
            except OSError as exc:self.message("[WARNING] Could not save stop record: "+str(exc))
            for proc in (client,worker):
                try: stop_process(proc,cfg["runtime"]["shutdown_timeout_s"])
                except Exception as exc: self.message("[ERROR] Cleanup failure: "+str(exc))
    def run(self):
        cfg=load(self.path)
        with device_lock(cfg):
            options=diagnostic_options(cfg)
            if self.diagnostics is None:self.diagnostics=options["enabled"]
            if self.important_only is None:self.important_only=options["important_only"]
            if self.diagnostics or self.important_only:
                from .logs import Capture
                self.capture=Capture(ROOT/"logs"/cfg["device_id"],self.important_only,options)
                self.message("[DIAGNOSTICS] Logs: "+str(ROOT/"logs"/cfg["device_id"]))
            delay=cfg["recovery"]["retry_initial_s"]
            old={s:signal.signal(s,self.stop) for s in (signal.SIGINT,signal.SIGTERM)}
            try:
                while not self.stopped.is_set():
                    try:
                        cfg=load(self.path)
                        self.preflight(cfg)
                        self.run_once(cfg)
                        delay=cfg["recovery"]["retry_initial_s"]
                    except Exception as exc:
                        self.message(f"[RECOVERY] {type(exc).__name__}: {exc}; retry in {delay}s")
                        try: write_json(state_dir(cfg)/"recovery.json",{"at":time.time(),"error":str(exc),"retry_s":delay})
                        except OSError as failure:self.message("[WARNING] Could not save recovery record: "+str(failure))
                        self.wait_for_recovery(cfg,exc)
                    if self.stopped.wait(delay): break
                    delay=min(delay*2,cfg["recovery"]["retry_max_s"])
            finally:
                for s,handler in old.items(): signal.signal(s,handler)
                if self.capture: self.capture.close(); self.capture=None
