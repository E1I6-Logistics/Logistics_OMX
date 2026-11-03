import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from omx_manager import config as C, unload_resume as U
from omx_manager.discard_order import discard
from omx_manager.journal import JobJournal
from test_workflow_recovery import cfg


def seed(cfg,tmp_path):
    cfg['device_id']='omx3';cfg['role']='unload';cfg['unload']['inspection_enabled']=False
    journal=JobJournal(tmp_path/'jobs.json');journal.begin('old',{'E':3})
    journal.begin('done',{'E':1});journal.finish('done',True,'already completed')
    checkpoint=U.prepare(cfg,tmp_path,'old',{'E':3})
    checkpoint=U.mark(tmp_path,checkpoint,'IN_FLIGHT',completed=1)
    C.write_json(tmp_path/'workstation_command.json',{'job_id':'old'})
    return journal,checkpoint


def test_discard_changed_model_fails_old_id_preserves_count_and_never_moves(cfg,tmp_path):
    journal,checkpoint=seed(cfg,tmp_path)
    cfg['models']['active_profile']='model-changed'
    publish=Mock();report=discard(cfg,tmp_path,confirmed=True,publish=publish)
    assert report['failed_jobs']==['old'] and report['motor_writes'] is False and report['service_started'] is False
    assert journal.lookup('old',{'E':3})['success'] is False
    assert journal.lookup('done',{'E':1})['success'] is True
    assert json.loads((Path(report['archive'])/'unload_checkpoint.json').read_text())['completed_cycles']==1
    assert not (tmp_path/'unload_checkpoint.json').exists() and not (tmp_path/'workstation_command.json').exists()
    assert publish.call_args.args[1]['job_id']=='old' and publish.call_args.args[1]['success'] is False
    with pytest.raises(ValueError,match='replay'):journal.begin('old',{'E':3})


def test_failed_broker_notification_is_durable_and_repeatable(cfg,tmp_path):
    seed(cfg,tmp_path)
    report=discard(cfg,tmp_path,confirmed=True,publish=Mock(side_effect=OSError('broker down')))
    assert report['results_pending']==1
    send=Mock();again=discard(cfg,tmp_path,confirmed=True,publish=send)
    assert again['results_pending']==0 and send.call_args.args[1]['job_id']=='old'


def test_unconfirmed_discard_changes_nothing(cfg,tmp_path):
    seed(cfg,tmp_path);before=(tmp_path/'jobs.json').read_bytes()
    with pytest.raises(C.ConfigError,match='confirm-discard'):discard(cfg,tmp_path)
    assert (tmp_path/'jobs.json').read_bytes()==before


def test_power_cut_intent_blocks_automatic_resume(cfg,tmp_path):
    seed(cfg,tmp_path);C.write_json(tmp_path/'discard_pending.json',{})
    with pytest.raises(C.ConfigError,match='omx_manual discard'):U.pending(cfg,tmp_path)


def test_discard_rejects_corrupt_journal_without_deleting_evidence(cfg,tmp_path):
    seed(cfg,tmp_path);C.write_json(tmp_path/'jobs.json',{'bad':'invalid'})
    before=(tmp_path/'unload_checkpoint.json').read_bytes()
    with pytest.raises(C.ConfigError,match='기록 형식'):discard(cfg,tmp_path,confirmed=True,publish=Mock())
    assert (tmp_path/'unload_checkpoint.json').read_bytes()==before
