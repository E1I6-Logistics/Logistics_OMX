"""Explicit operator failure of old unload jobs. Never move or restart hardware."""
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from .config import ConfigError, write_json, atomic_write
from .journal import JobJournal

FILES = ('unload_checkpoint.json', 'workstation_command.json', 'workstation_status.json',
         'unload_progress.json', 'inventory.json', 'resume_request.json', 'auto_restart.json', 'recovery_wait.json')

def notify(cfg, payload):
    command = shutil.which('mosquitto_pub')
    if not command:raise RuntimeError('mosquitto_pub가 없습니다')
    subprocess.run([command, '-h', cfg['runtime']['broker_host'], '-p', str(cfg['runtime']['broker_port']),
                    '-t', cfg['device_id']+'/result', '-q', '1', '-m', json.dumps(payload)],
                   check=True, timeout=8, capture_output=True)

def discard(cfg, directory, *, confirmed=False, publish=None):
    if not confirmed:raise ConfigError('이전 주문 실패 처리는 --confirm-discard가 필요합니다')
    if cfg['device_id'] not in ('omx3','omx4') or cfg['role']!='unload':
        raise ConfigError('이 명령은 OMX3·4 하차 주문 정리 전용입니다')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    journal=JobJournal(directory/'jobs.json');jobs=journal.read()
    if any(not isinstance(row,dict) or row.get('state') not in ('RUNNING','DONE','INTERRUPTED') for row in jobs.values()):
        raise ConfigError('주문 기록 형식 오류. 자동 삭제하지 않습니다')
    intent_path=directory/'discard_pending.json';outbox_path=directory/'discard_results_pending.json'
    intent=json.loads(intent_path.read_text()) if intent_path.exists() else None
    if intent is None:
        ids=[key for key,row in jobs.items() if row['state']=='RUNNING']
        payloads=[]
        for key in ids:
            items=json.loads(jobs[key]['signature'])
            if not isinstance(items,dict) or JobJournal.signature(items)!=jobs[key]['signature']:
                raise ConfigError('이전 주문 품목 기록 오류')
            payloads.append(dict(job_id=key,success=False,message='사용자 요청으로 이전 미완료 하차 주문을 종료했습니다. 자동 재실행 없음',
                                 count_kind='motion_cycles',physical_unloading_verified=False))
        intent=dict(id=uuid.uuid4().hex,device=cfg['device_id'],job_ids=ids,payloads=payloads,at=time.time())
        archive=directory/'archive'/('discard_'+intent['id']);archive.mkdir(parents=True)
        for name in ('jobs.json',)+FILES:
            source=directory/name
            if source.exists():atomic_write(archive/name,source.read_text())
        write_json(intent_path,intent)
    if (not isinstance(intent,dict) or intent.get('device')!=cfg['device_id']
            or not isinstance(intent.get('id'),str) or len(intent['id'])!=32 or not intent['id'].isalnum()
            or not isinstance(intent.get('job_ids'),list) or not isinstance(intent.get('payloads'),list)):
        raise ConfigError('수동 정리 복구 기록 오류')
    if any(key not in intent['job_ids'] for key,row in jobs.items() if row['state']=='RUNNING'):
        raise ConfigError('정리 도중 다른 주문이 추가됐습니다. 실행기를 중지하세요')
    for key in intent['job_ids']:
        if key not in jobs:raise ConfigError('정리 대상 주문 기록이 없습니다')
        if jobs[key]['state']=='RUNNING':
            jobs[key].update(state='DONE',success=False,message='Operator discarded interrupted unload; no replay',finished_at=time.time())
        elif jobs[key].get('success') is not False:raise ConfigError('완료된 주문은 실패로 변경하지 않습니다')
    write_json(directory/'jobs.json',jobs)  # Terminal IDs stay permanently fenced against replay.
    outbox=json.loads(outbox_path.read_text()) if outbox_path.exists() else []
    if not isinstance(outbox,list):raise ConfigError('결과 전송 대기 기록 오류')
    merged={row['job_id']:row for row in outbox}
    merged.update({row['job_id']:row for row in intent['payloads']})
    outbox=list(merged.values());write_json(outbox_path,outbox)
    archive=directory/'archive'/('discard_'+intent['id'])
    for name in FILES:
        source=directory/name
        if source.exists():
            if not (archive/name).exists():atomic_write(archive/name,source.read_text())
            source.unlink()
    result=dict(device=cfg['device_id'],failed_jobs=intent['job_ids'],archive=str(archive),
                motor_writes=False,service_started=False,at=time.time())
    write_json(directory/'operator_discard.json',result);intent_path.unlink()
    publish=publish or notify
    errors=[]
    for row in list(outbox):
        try:publish(cfg,row)
        except Exception as exc:errors.append(str(exc));continue
        outbox.remove(row);write_json(outbox_path,outbox)
    result.update(results_pending=len(outbox),notify_errors=errors)
    write_json(directory/'operator_discard.json',result)
    return result
