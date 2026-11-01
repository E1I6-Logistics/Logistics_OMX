"""SDK-free fault categories; only explicit transient faults permit auto retry."""
import errno
import json
import os
import time
from pathlib import Path
from .config import ConfigError,write_json

AUTOMATIC={'TRANSPORT','CAMERA','WATCHDOG','MQTT','PROCESS_EXIT','RESOURCE','JOB_TIMEOUT','CLEANUP'}


class ManagedFault(RuntimeError):
    def __init__(self,message,code):
        super().__init__(message);self.fault_code=code


def classify(error):
    if isinstance(error,ManagedFault):return error.fault_code
    chain=[];seen=set();current=error
    while current is not None and id(current) not in seen:
        seen.add(id(current));chain.append(current);current=current.__cause__ or current.__context__
    message=' '.join(str(e).lower() for e in chain)
    # A bus/SDK wrapper must never hide a motor or pose refusal.
    for words,code in (
        (('hardware error','mode mismatch','mode changed','calibration differs','calibration changed','outside trained calibration'),'MOTOR_FAULT'),
        (('torque off','mixed/changed torque','unverified startup','startup torque enable already'),'TORQUE_OFF'),
        (('outside approved','outside registered','outside startup','no unique tested','unknown pose','startup pose is moving'),'POSE_UNKNOWN'),
        (('home movement timed out','following error','position changed at torque','arm moved at startup'),'HOME_FOLLOWING'),
        (('exposure restoration pending','camera restoration unverified'),'CAMERA_RESTORE'),
        (('child process cleanup pending',),'CLEANUP'),
        (('automatic restart limit','invalid automatic restart'),'RETRY_LIMIT')):
        if any(word in message for word in words):return code
    if any(isinstance(e,ConfigError) for e in chain):return 'CONFIGURATION'
    if any(isinstance(e,MemoryError) or isinstance(e,OSError) and e.errno==errno.ENOMEM for e in chain):return 'RESOURCE'
    if any(isinstance(e,OSError) and e.errno in (errno.EIO,errno.ENODEV,errno.ENXIO,errno.EBUSY) for e in chain):return 'TRANSPORT'
    if any(word in message for word in ('pickup timeout','workstation job timeout','workstation timeout')):return 'JOB_TIMEOUT'
    if any(word in message for word in ('failed to open opencvcamera','failed to capture','camera disconnected','camera read','camera frame','realsense')):return 'CAMERA'
    if any(isinstance(e,ConnectionError) for e in chain):return 'TRANSPORT'
    if any(isinstance(e,TimeoutError) for e in chain):return 'WATCHDOG'
    return 'UNKNOWN'


def record_rollout_fault(error):
    path=os.environ.get('OMX_ROLLOUT_FAULT')
    if path:write_json(path,dict(code=classify(error),message=str(error),pid=os.getpid(),
                                session=os.environ.get('OMX_SESSION'),at=time.time()))


def worker_failure(path,pid,session,message):
    # Ignore previous sessions/PIDs and malformed reports, including unknown codes.
    try:
        row=json.loads(Path(path).read_text())
        codes=AUTOMATIC|{'MOTOR_FAULT','TORQUE_OFF','POSE_UNKNOWN','HOME_FOLLOWING','CAMERA_RESTORE','CONFIGURATION','UNKNOWN'}
        if row['pid']==pid and row['session']==session and row['code'] in codes:
            return ManagedFault(message+': '+row['message'],row['code'])
    except (OSError,ValueError,KeyError,TypeError):pass
    return ManagedFault(message,'PROCESS_EXIT')
