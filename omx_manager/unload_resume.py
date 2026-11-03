"""Durable OMX3/4 cycle boundaries. An in-flight cycle is never inferred complete."""
import hashlib
import json
import time
from pathlib import Path
from .config import ConfigError, write_json
from .journal import JobJournal

SAFE_PHASES = {'PREPARED', 'BOUNDARY', 'COMPLETE'}


def enabled(cfg):
    from .basket import options
    return (cfg.get('device_id') in ('omx3', 'omx4') and cfg.get('role') == 'unload'
            and cfg.get('recovery', {}).get('resume_unload', True)
            and not options(cfg)['inspection_enabled'])


def binding(cfg):
    # Changing the model, camera, calibration, Home or detector invalidates continuation.
    value = {key: cfg.get(key) for key in ('device_id', 'hardware', 'home', 'models', 'counting', 'workflow', 'unload')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def checkpoint_path(directory):
    return Path(directory) / 'unload_checkpoint.json'


def pending(cfg, directory, *, require_safe=False):
    if (Path(directory)/'discard_pending.json').exists():
        raise ConfigError('이전 주문 실패 처리 미완료: 서비스 중지 후 omx_manual discard를 다시 실행하세요')
    if not enabled(cfg):
        return None
    path = checkpoint_path(directory)
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text())
        if not isinstance(row, dict):
            raise ValueError('checkpoint must be an object')
        job = row['job_id']
        items = row['items']
        target, completed = row['requested_cycles'], row['completed_cycles']
        from .workstation import expected_quantity
        if (not isinstance(job, str) or not job or len(job) > 200
                or type(target) is not int or not 1 <= target <= 8
                or type(completed) is not int or not 0 <= completed <= target
                or expected_quantity(items) != target
                or row.get('phase') not in SAFE_PHASES | {'IN_FLIGHT'}
                or (row['phase'] == 'COMPLETE') != (completed == target)):
            raise ValueError('invalid cycle identity/count/phase')
        record = JobJournal(Path(directory) / 'jobs.json').lookup(job, items)
        if not record:
            raise ValueError('missing job identity')
        if record.get('state') != 'RUNNING':
            return None  # Terminal jobs are never resurrected, including failed IDs.
        if row.get('device') != cfg['device_id'] or row.get('binding') != binding(cfg):
            raise ValueError('model/Home/camera configuration changed')
        if require_safe and row['phase'] == 'IN_FLIGHT':
            raise ConfigError('하차 사이클 중단: 실제 완료 여부 불명확. 물품·팔 확인 후 omx_resume으로 해당 사이클 재시도를 승인하세요')
        return row
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError('하차 재개 기록 확인 필요: ' + str(exc)) from exc


def prepare(cfg, directory, job_id, items):
    from .workstation import expected_quantity
    from .workstation_cycles import require_count
    target = require_count(expected_quantity(items))
    row = dict(device=cfg['device_id'], job_id=job_id, items=items,
               binding=binding(cfg), requested_cycles=target, completed_cycles=0,
               phase='PREPARED', at=time.time(), physical_unloading_verified=False)
    write_json(checkpoint_path(directory), row)
    return row


def mark(directory, row, phase, *, completed=None):
    updated = dict(row, phase=phase, at=time.time())
    if completed is not None:
        updated['completed_cycles'] = completed
    write_json(checkpoint_path(directory), updated)
    return updated


def authorize_retry(cfg, directory):
    row = pending(cfg, directory)
    if row and row['phase'] == 'IN_FLIGHT':
        # Explicit operator request only; never count an uncertain physical operation.
        row = mark(directory, row, 'BOUNDARY')
        row['operator_retry_at'] = time.time()
        write_json(checkpoint_path(directory), row)
    return row


def reconcile(cfg, directory, *, operator_confirmed=False):
    row = authorize_retry(cfg, directory) if operator_confirmed else pending(cfg, directory, require_safe=True)
    if row:
        jobs = JobJournal(Path(directory) / 'jobs.json').read()
        if [key for key, value in jobs.items() if value.get('state') == 'RUNNING'] != [row['job_id']]:
            raise ConfigError('하차 재개 주문이 둘 이상이거나 기록이 일치하지 않습니다')
    return row
