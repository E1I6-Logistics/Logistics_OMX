"""Durable idle reconciliation and a bounded per-boot automatic restart budget."""
import json
import time
import uuid
from pathlib import Path
from .config import ConfigError,write_json
from .startup_arming import boot_id


def reconcile(cfg,directory):
    """Archive active IPC, fail running IDs, preserve duplicate-command rejection.

    No Home/calibration/model writes, cargo claims, motor writes or job replay.
    A durable intent makes interrupted cleanup resume after another power cut.
    """
    if not cfg['recovery'].get('restart_to_idle',False):return False
    directory=Path(directory);intent=directory/'idle_reset_pending.json'
    names=('inventory.json','jobs.json','workstation_command.json','workstation_status.json','unload_progress.json')
    jobs=json.loads((directory/'jobs.json').read_text()) if (directory/'jobs.json').exists() else {}
    if not isinstance(jobs,dict) or any(not isinstance(row,dict) for row in jobs.values()):
        raise ConfigError('Invalid job journal; automatic idle reset refused')
    if any(row.get('state') not in ('RUNNING','DONE','INTERRUPTED') for row in jobs.values()):
        raise ConfigError('Invalid job state; automatic idle reset refused')
    inventory=json.loads((directory/'inventory.json').read_text()) if (directory/'inventory.json').exists() else None
    if inventory is not None and (not isinstance(inventory,dict) or inventory.get('capacity')!=8
            or type(inventory.get('remaining')) is not int or type(inventory.get('total_completed')) is not int
            or not 0<=inventory['remaining']<=8 or inventory['total_completed']!=8-inventory['remaining']):
        raise ConfigError('Invalid inventory state; automatic idle reset refused')
    running=[key for key,row in jobs.items() if row.get('state')=='RUNNING']
    if intent.exists():
        record=json.loads(intent.read_text())
        if not isinstance(record,dict) or record.get('device')!=cfg['device_id'] or not isinstance(record.get('id'),str) or not record['id'].isalnum():
            raise ConfigError('Invalid idle reset intent; inspect locally')
    else:
        if not running and not (inventory and inventory['remaining']):return False
        record=dict(id=uuid.uuid4().hex,device=cfg['device_id'],at=time.time(),job_ids=running)
        archive=directory/'archive'/('restart_'+record['id']);archive.mkdir(parents=True,exist_ok=True)
        # Save all evidence before modifying any active state.
        for name in names:
            path=directory/name
            if path.exists():write_json(archive/name,json.loads(path.read_text()))
        write_json(intent,record)
    at=time.time()
    for key in running:
        jobs[key].update(state='INTERRUPTED',success=False,finished_at=at,
                         message='Interrupted order failed on restart; no replay; cargo unverified')
    if running:write_json(directory/'jobs.json',jobs)
    # IDs/signatures remain in jobs.json, including earlier DONE/INTERRUPTED IDs.
    for name in names:
        if name!='jobs.json':(directory/name).unlink(missing_ok=True)
    write_json(directory/'interrupted_order.json',dict(record,action='FAILED_NO_REPLAY',
               physical_transfer_verified=False,archive=str(directory/'archive'/('restart_'+record['id']))))
    intent.unlink()
    return True


def budget(cfg,directory,*,consume=None):
    if not cfg['recovery'].get('restart_to_idle',False):return 0
    path=Path(directory)/'auto_restart.json';identity=boot_id();attempts=0
    if path.exists():
        row=json.loads(path.read_text())
        if (not isinstance(row,dict) or row.get('device')!=cfg['device_id'] or type(row.get('attempts')) is not int or row['attempts']<0
                or not isinstance(row.get('boot_id'),str) or not row['boot_id']):
            raise ConfigError('Invalid automatic restart record; inspect locally')
        if row.get('boot_id')==identity:attempts=row['attempts']
    limit=cfg['recovery'].get('auto_retry_limit',3)
    if attempts>=limit:raise ConfigError('Automatic restart limit reached; inspect and request resume')
    if consume:
        attempts+=1
        write_json(path,dict(device=cfg['device_id'],boot_id=identity,attempts=attempts,limit=limit,
                             code=consume,at=time.time(),job_replayed=False))
    return attempts
