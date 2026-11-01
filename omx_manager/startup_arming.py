"""Opt-in startup torque enable at a stationary registered Home, never a route."""
import json
import logging
import time
from pathlib import Path
from .config import JOINTS,write_json
from .home import errors,stable_pose

logger=logging.getLogger(__name__)


class StartupArmRefused(RuntimeError):pass


def boot_id():
    value=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if not value:raise StartupArmRefused('Cannot identify this boot; startup torque enable refused')
    return value


def check_bus(device,torque):
    device.health()
    for joint in JOINTS:
        if device.bus.read('Operating_Mode',joint)!=(5 if joint=='gripper' else 4):
            raise StartupArmRefused('Motor mode mismatch; startup torque enable refused')
        if device.bus.read('Torque_Enable',joint)!=torque:
            raise StartupArmRefused('Mixed/changed torque state; inspect locally before enabling')


def require_verified_journal(cfg,directory):
    """A failed/partial enable blocks model startup even if torque stayed ON."""
    path=Path(directory)/'startup_arm.json'
    if not path.exists():return
    try:previous=json.loads(path.read_text())
    except (OSError,ValueError):raise StartupArmRefused('Invalid startup torque journal; inspect locally')
    if (not isinstance(previous,dict) or previous.get('device')!=cfg['device_id']
            or previous.get('phase') not in ('ARMING','ARMED','FAILED','RECONCILE_REQUIRED')):
        raise StartupArmRefused('Invalid startup torque journal; inspect locally')
    if previous['phase']!='ARMED':
        raise StartupArmRefused('Unverified startup torque/order state; inspect locally and request resume')


def arm_at_home(device,cfg,directory,*,allow_motion,rollout_started,attempted,mark_attempt,
                stopped,reconcile_required=False,clock=time.monotonic,sleep=time.sleep):
    recovery=cfg['recovery']
    if not recovery.get('startup_arm_at_home',False):
        raise StartupArmRefused('Torque OFF: support arm and use local arm command; automatic startup enable is disabled')
    if not allow_motion or rollout_started:
        raise StartupArmRefused('Torque OFF after startup or during passive recheck; local arm command required')
    if attempted:raise StartupArmRefused('Startup torque enable already attempted; inspect locally, no automatic retry')
    path=Path(directory)/'startup_arm.json';identity=boot_id()
    require_verified_journal(cfg,directory)
    if reconcile_required:
        write_json(path,dict(phase='RECONCILE_REQUIRED',boot_id=identity,device=cfg['device_id'],at=time.time(),reason='Interrupted physical pickup; inspect cargo before startup torque enable'))
        raise StartupArmRefused('Interrupted physical pickup; inspect cargo and request resume before enabling torque')
    limit=recovery.get('startup_arm_tolerance',2.)
    def pose_check(q):
        outside={joint:round(value,4) for joint,value in errors(q,cfg['home']['target']).items() if value>limit}
        if outside:raise StartupArmRefused('Outside startup Home area; local positioning required: '+str(outside))
    def stop_check():
        if stopped():raise InterruptedError('Operator stopped; no startup torque enable')
    samples=[];last=None
    check_bus(device,0)
    for index in range(3):
        stop_check();q=device.read();now=clock();pose_check(q)
        if last is not None and not 0<now-last<=cfg['home']['max_sample_gap_s']:
            raise StartupArmRefused('Startup pose feedback gap; no torque enable')
        samples.append(q);last=now
        if index<2:sleep(min(.04,cfg['home']['max_sample_gap_s']/4))
    try:stable_pose(samples,min(cfg['home']['capture_max_span'],cfg['home']['still_delta']))
    except ValueError as exc:raise StartupArmRefused('Startup pose is moving; no torque enable: '+str(exc)) from exc
    check_bus(device,0);stop_check()
    record=dict(phase='ARMING',boot_id=identity,device=cfg['device_id'],at=time.time(),
                home_binding=cfg['home']['binding'],observed=samples[-1],tolerance=limit)
    mark_attempt();write_json(path,record)
    try:
        stop_check()
        def guard(q):
            pose_check(q)
            if max(abs(q[j]-samples[-1][j]) for j in JOINTS)>cfg['home']['still_delta']:
                raise StartupArmRefused('Startup pose changed before goal latch; no torque enable')
        # Existing manual arm operation re-reads position, latches ALL current
        # goals, then enables torque. No mode/PID/calibration/EEPROM writes.
        device.arm(stopped=stopped,pose_guard=guard)
        check_bus(device,1);after=device.read();pose_check(after)
        if max(abs(after[j]-samples[-1][j]) for j in JOINTS)>cfg['home']['arrival_tolerance']:
            raise StartupArmRefused('Arm moved at startup torque enable; inspect locally')
        goals=device.bus.sync_read('Goal_Position')
        if max(abs(goals[j]-after[j]) for j in JOINTS)>cfg['home']['arrival_tolerance']:
            raise StartupArmRefused('Startup hold goal/feedback mismatch; inspect locally')
        stop_check()
        record.update(phase='ARMED',at=time.time(),observed_after=after)
        write_json(path,record)
        logger.info('[STARTUP] Torque enabled at registered Home; current goals latched; no job replay')
        return after
    except BaseException as exc:
        record.update(phase='FAILED',at=time.time(),reason=str(exc))
        try:write_json(path,record)
        except OSError:logger.exception('[STARTUP] Failed to record torque verification failure')
        raise
