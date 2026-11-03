"""Restart real cycle/IPC code with durable journals; never connect hardware."""
import copy
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from omx_manager import config as C, unload_resume as U, runtime as R, restart_policy as P
from omx_manager.journal import JobJournal
from test_unload_without_inspection import prepare
from test_workflow_recovery import cfg
from test_runtime_reliability import load_function

ROOT=Path(__file__).resolve().parents[1]


def seed(cfg, directory, count=3, completed=1, phase='BOUNDARY'):
    cfg['unload']['inspection_enabled']=False
    cfg['recovery']['resume_unload']=True
    items={'E' if cfg['device_id']=='omx3' else 'F':count}
    journal=JobJournal(directory/'jobs.json');journal.begin('job',items)
    row=U.prepare(cfg,directory,'job',items)
    return journal,U.mark(directory,row,phase,completed=completed)


@pytest.mark.parametrize('device',['omx3','omx4'])
def test_restart_runs_only_remaining_cycles_and_keeps_original_identity(cfg,tmp_path,monkeypatch,device):
    cfg['device_id']=device
    run,phases,sent,done,path=prepare(cfg,tmp_path,monkeypatch,expected=3)
    journal,row=seed(cfg,tmp_path)
    assert P.reconcile(cfg,tmp_path) is False  # do not fail or discard this ID
    run()
    assert phases.count('RUNNING')==2 and sent
    assert done[0]['job_id']=='job' and done[0]['completed_cycles']==3
    assert U.pending(cfg,tmp_path)['phase']=='COMPLETE'
    assert journal.lookup('job',row['items'])['state']=='RUNNING'  # MQTT commits terminal result


def test_complete_checkpoint_never_executes_another_motion(cfg,tmp_path,monkeypatch):
    run,phases,sent,done,path=prepare(cfg,tmp_path,monkeypatch,expected=3)
    seed(cfg,tmp_path,completed=3,phase='COMPLETE')
    run()
    assert not sent and 'RUNNING' not in phases and done[0]['completed_cycles']==3


def test_inflight_is_not_replayed_until_explicit_operator_resume(cfg,tmp_path,monkeypatch):
    run,phases,sent,done,path=prepare(cfg,tmp_path,monkeypatch,expected=3)
    journal,row=seed(cfg,tmp_path,phase='IN_FLIGHT')
    with pytest.raises(C.ConfigError,match='omx_resume'):run()
    assert not sent and not done
    assert U.pending(cfg,tmp_path)['completed_cycles']==1
    monkeypatch.setattr(R,'state_dir',lambda _:tmp_path)
    R.acknowledge_recovery(cfg)
    assert U.pending(cfg,tmp_path,require_safe=True)['completed_cycles']==1
    assert journal.lookup('job',row['items'])['state']=='RUNNING'
    assert U.pending(cfg,tmp_path)['operator_retry_at']>0


def test_changed_model_or_terminal_job_cannot_be_resurrected(cfg,tmp_path):
    journal,row=seed(cfg,tmp_path)
    cfg['hardware']['robot_id']='different-arm'
    with pytest.raises(C.ConfigError,match='configuration changed'):U.pending(cfg,tmp_path)
    cfg['hardware']['robot_id']='restored-but-not-same'
    journal.finish('job',False,'cancelled')
    assert U.pending(cfg,tmp_path) is None


@pytest.mark.parametrize('device',['omx1','omx2'])
def test_pickup_and_inspection_modes_never_resume(cfg,tmp_path,device):
    cfg['device_id']=device;cfg['role']='pickup'
    assert U.pending(cfg,tmp_path) is None
    cfg['device_id']='omx4';cfg['role']='unload';cfg['unload']['inspection_enabled']=True
    assert not U.enabled(cfg)


def test_corrupt_counts_and_duplicate_running_orders_fail_closed(cfg,tmp_path):
    journal,row=seed(cfg,tmp_path)
    row['completed_cycles']=4;C.write_json(U.checkpoint_path(tmp_path),row)
    with pytest.raises(C.ConfigError):U.pending(cfg,tmp_path)
    row['completed_cycles']=1;C.write_json(U.checkpoint_path(tmp_path),row)
    journal.begin('other',{'F':1})
    with pytest.raises(C.ConfigError,match='둘 이상'):U.reconcile(cfg,tmp_path)


def mqtt_functions(monkeypatch,cfg,tmp_path):
    monkeypatch.setenv('OMX_UNLOAD_RESUME','1')
    monkeypatch.setenv('OMX_ABORT_FILE',str(tmp_path/'abort'))
    # Load actual functions, bypass argument parsing, ROS, motors and broker connection.
    env=dict(os=os,Path=Path,threading=threading,robot_id='omx4',WORKSTATION_IDS=('omx3','omx4'),
             PICKUP_ITEMS={},journal=JobJournal(tmp_path/'jobs.json'),state_path=tmp_path/'inventory.json',
             current_job_id=None,current_orders=None,current_state='IDLE',job_lock=threading.Lock(),
             unload_resume_config=lambda:cfg,send_status=Mock(),send_result=Mock(),workstation_ready=lambda:True,
             execute_unload=Mock(side_effect=RuntimeError('USB disconnected')))
    fn=load_function('lerobot_unified/omx_mqtt_client.py','run_job',namespace=env)
    return fn,fn.__globals__


def test_transient_failure_does_not_publish_terminal_failure_or_lose_progress(cfg,tmp_path,monkeypatch):
    journal,row=seed(cfg,tmp_path)
    fn,env=mqtt_functions(monkeypatch,cfg,tmp_path)
    env['job_lock'].acquire();fn('job',row['items'])
    env['send_result'].assert_not_called()
    assert journal.lookup('job',row['items'])['state']=='RUNNING'
    assert U.pending(cfg,tmp_path)['completed_cycles']==1
    assert env['current_state']=='ERROR' and (tmp_path/'abort').exists()


def test_reconnected_mqtt_reserves_pending_job_before_new_commands(cfg,tmp_path,monkeypatch):
    journal,row=seed(cfg,tmp_path)
    fn,env=mqtt_functions(monkeypatch,cfg,tmp_path)
    threads=[]
    env['threading']=NS(Thread=lambda **kw:NS(start=lambda:threads.append(kw)))
    env['run_job']=fn
    restore=load_function('lerobot_unified/omx_mqtt_client.py','restore_pending_unload',namespace=env)
    restore();restore()
    assert len(threads)==1 and threads[0]['args']==('job',row['items'])
    assert restore.__globals__['current_state']=='WORKING' and env['job_lock'].locked()
