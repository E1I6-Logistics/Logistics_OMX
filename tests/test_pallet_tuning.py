"""Human labels and real worker/file path, synthetic RGB only; no hardware."""
import copy
import json
import re
import shutil
import subprocess
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import numpy as np
import pytest

from omx_manager import config as C,pallet as P,pallet_cli as CLI,viewer as V
from omx_manager.pallet_tuning import Tuning,settings_hash,apply_parameters,validate_parameters
from omx_manager.pallet_tuning_page import PAGE
from test_manager import cfg
from test_pallet_stock import stock,producer


@pytest.fixture
def tuning(stock,monkeypatch):
    cfg,frame,directory=stock
    cfg['pallet_stock']['precision_enabled']=True
    path=directory/'test-config.yaml';C.save(path,cfg)
    monkeypatch.setenv('OMX_MANAGER_CONFIG',str(path))
    worker=producer(stock);worker.gate();worker.save(worker.inspector.unknown('test ready'))
    original=CLI.request
    def request(cfg,action,item=None,*,payload=None,consume=None,**kw):
        # Exercise Worker.command, preserving the same exclusive request/copy scope.
        from omx_manager.hardware import Lock
        with Lock(directory/'pallet_cli.lock'):
            worker.observe({j+'.pos':0. for j in C.JOINTS})
            C.write_json(directory/'pallet_request.json',dict(action=action,item=item,payload=payload,
                session='s',token='a'*32,at=time.time()))
            worker.command()
            reply=json.loads((directory/'pallet_reply.json').read_text())
            if not reply['ok']:raise RuntimeError(reply['message'])
            return consume(reply) if consume else reply
    monkeypatch.setattr(CLI,'request',request)
    return Tuning(cfg,path),worker,frame,path,directory


def collect(tuning,label,count=3,item='A'):
    tuner,worker,frame,path,directory=tuning
    worker.robot.cameras['front'].read_latest_with_timestamp=lambda **kw:(frame.copy(),time.monotonic())
    original=frame.copy()
    if label=='PRESENT':frame[50:70,60:80]=60
    try:
        for _ in range(count):
            row=tuner.capture(item);tuner.label(row['id'],label)
    finally:frame[:]=original


def parameters(tuner,item='A'):return tuner.info()['items'][item]['parameters']


def test_item_override_is_validated_and_only_used_by_selected_item(stock):
    cfg,frame,_=stock;before=copy.deepcopy(cfg)
    changed=validate_parameters(cfg,'A',{'pixel_threshold':30.,'weak_pixel_threshold':9.})
    assert P.item_options(P.options(changed),'A')['pixel_threshold']==30
    assert P.item_options(P.options(changed),'B')['pixel_threshold']==20
    assert P.signature(cfg,'A')==P.signature(changed,'A') and cfg==before
    changed['pallet_stock']['items']['A']['parameters']['weak_pixel_threshold']=35
    with pytest.raises(C.ConfigError):P.validate(changed)
    for key in ('roi','enabled','stable_samples','camera','max_frame_age_s'):
        with pytest.raises(ValueError):validate_parameters(cfg,'A',{key:1})


def test_capture_label_comparison_and_apply_uses_production_path(tuning):
    tuner,worker,frame,path,directory=tuning
    before=C.load(path);ref=(directory/'pallet_refs/A.npz').read_bytes();inventory=(directory/'inventory.json').read_bytes()
    collect(tuning,'EMPTY');collect(tuning,'PRESENT')
    args=parameters(tuner);report=tuner.evaluate('A',args)
    assert report['can_save'] and report['false_empty']==0 and len(report['rows'])==6
    suggested=tuner.suggest('A',args)
    assert suggested['report']['can_save']
    result=tuner.apply('A',suggested['parameters'])
    after=C.load(path)
    assert '즉시 적용' in result['message']
    for key in before:
        if key!='pallet_stock':assert after[key]==before[key]
    assert after['pallet_stock']['items']['B']==before['pallet_stock']['items']['B']
    assert after['pallet_stock']['items']['A']['roi']==before['pallet_stock']['items']['A']['roi']
    assert worker.spec==P.options(after) and worker.inspector.state=={}
    assert (directory/'pallet_refs/A.npz').read_bytes()==ref
    assert (directory/'inventory.json').read_bytes()==inventory
    assert list((path.parent/'backups').glob('*.yaml'))
    assert len(list((directory/'pallet_tuning').glob('*.npz')))==6
    assert tuner.samples==[]


def test_save_rejects_insufficient_data_or_misclassified_present(tuning):
    tuner,*_=tuning;collect(tuning,'EMPTY',2);collect(tuning,'PRESENT',2)
    with pytest.raises(ValueError,match='3장'):tuner.apply('A',parameters(tuner))
    collect(tuning,'EMPTY',1);collect(tuning,'PRESENT',1)
    dangerous=dict(parameters(tuner),pixel_threshold=80.,weak_pixel_threshold=39.,min_component_pixels=500,
                   empty_component_pixels=200,empty_ratio=.02)
    # Explicitly corrupt a label to demonstrate server-side mismatch prevents saving.
    tuner.samples[-1]['label']='EMPTY'
    with pytest.raises(ValueError):tuner.apply('A',dangerous)


def test_order_start_restart_reference_change_and_expired_data_rejected(tuning):
    tuner,worker,frame,path,directory=tuning
    collect(tuning,'EMPTY');collect(tuning,'PRESENT');args=parameters(tuner)
    C.write_json(directory/'inventory.json',dict(remaining=1,order_started_at_epoch=11))
    before=path.read_bytes()
    with pytest.raises(RuntimeError,match='주문'):tuner.apply('A',args)
    assert path.read_bytes()==before
    C.write_json(directory/'inventory.json',dict(remaining=0,order_started_at_epoch=11))
    C.write_json(directory/'pallet_stock.json',dict(session='another'))
    with pytest.raises(ValueError,match='재시작'):tuner.apply('A',args)
    C.write_json(directory/'pallet_stock.json',dict(session='s'))
    P.save_reference(worker.cfg,'A',[frame+1]*3)
    with pytest.raises(RuntimeError,match='빈 기준'):tuner.apply('A',args)
    tuner.samples[0]['at']-=1801
    with pytest.raises(ValueError,match='30분'):tuner.evaluate('A',args)


def test_capture_blocked_without_home_and_no_label_without_capture(tuning):
    tuner,worker,frame,path,directory=tuning
    worker.robot._omx_runtime_ready=False
    with pytest.raises(RuntimeError,match='준비'):tuner.capture('A')
    with pytest.raises(ValueError):tuner.label('bad','EMPTY')
    assert tuner.pending is None and tuner.samples==[]


def test_sample_limit_and_new_frame_and_expiration(tuning):
    tuner,worker,frame,*_=tuning
    row=tuner.capture('A');tuner.pending['at']-=121
    with pytest.raises(ValueError,match='만료'):tuner.label(row['id'],'EMPTY')
    collect(tuning,'EMPTY',12)
    row=tuner.capture('A')
    with pytest.raises(ValueError,match='최대'):tuner.label(row['id'],'EMPTY')
    tuner.dispatch({'action':'clear'})
    assert tuner.samples==[] and tuner.pending is None


def test_config_conflict_does_not_overwrite_external_edit(tuning):
    tuner,worker,frame,path,directory=tuning
    collect(tuning,'EMPTY');collect(tuning,'PRESENT');args=parameters(tuner)
    updated=C.load(path);updated['pallet_stock']['empty_ratio']=.01;C.save(path,updated)
    before=path.read_bytes()
    with pytest.raises(ValueError,match='설정 변경'):tuner.evaluate('A',args)
    assert path.read_bytes()==before


def test_request_consumer_errors_propagate_and_lock_is_released(stock,monkeypatch):
    cfg,_,directory=stock
    C.write_json(directory/'pallet_stock.json',dict(at=time.time(),session='s'))
    import uuid
    monkeypatch.setattr(uuid,'uuid4',lambda:type('ID',(),{'hex':'a'*32})())
    C.write_json(directory/'pallet_reply.json',dict(token='a'*32,ok=True))
    def consume(reply):raise ValueError('specific evidence mismatch')
    with pytest.raises(ValueError,match='specific evidence mismatch'):CLI.request(cfg,'diagnose','A',consume=consume)
    from omx_manager.hardware import Lock
    with Lock(directory/'pallet_cli.lock'):pass


def test_tuning_http_json_post_token_origin_and_readonly_view(tuning,monkeypatch,tmp_path):
    tuner,worker,frame,path,directory=tuning
    monkeypatch.setattr(V,'channel',lambda c:tmp_path/c['device_id'])
    server=V.ViewerServer(tuner.cfg,('127.0.0.1',0),config_path=path)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
    def http(route,body=None,headers=None):
        c=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            h={'Content-Type':'application/json','X-OMX-Tuning':server.tuning_token};h.update(headers or {})
            c.request('POST' if body is not None else 'GET',route,json.dumps(body) if body is not None else None,h)
            r=c.getresponse();return r.status,r.read()
        finally:c.close()
    try:
        assert http('/pallet-tuning')[0]==200
        code,data=http('/pallet-tuning/info');assert code==200 and json.loads(data)['items']['A']['roi']
        assert http('/pallet-tuning/api',{'action':'clear'},{'X-OMX-Tuning':'wrong'})[0]==400
        assert http('/pallet-tuning/api',{'action':'clear'},{'Origin':'http://malicious.example'})[0]==400
        code,data=http('/pallet-tuning/api',{'action':'capture','item':'A'});assert code==200
        identity=json.loads(data)['id']
        assert http('/pallet-tuning/image?id='+identity)[1].startswith(b'\x89PNG')
        assert http('/pallet-tuning/api',{'action':'label','id':identity,'label':'EMPTY'})[0]==200
        assert http('/pallet-tuning/api',{'action':'apply','item':'A','parameters':parameters(tuner),'confirm':True})[0]==400
        assert http('/status.json',{})[0]==501
    finally:server.shutdown();server.server_close();thread.join(1)


@pytest.mark.skipif(shutil.which('node') is None,reason='node unavailable')
def test_tuning_ui_javascript_parses():
    script=re.search(r'<script>(.*?)</script>',PAGE,re.S)[1]
    subprocess.run(['node','--check'],input=script,text=True,capture_output=True,check=True)
    assert "src=\"/robot\"" in PAGE and "'PRESENT'" in PAGE and "'EMPTY'" in PAGE
    assert 'confirm-motion' not in PAGE and 'capture --confirm-empty' not in PAGE


def test_holdout_present_failure_cannot_be_saved(tuning):
    tuner,*_=tuning;collect(tuning,'EMPTY');collect(tuning,'PRESENT')
    # An unseen tiny/pale product could resemble empty. Keep its human PRESENT
    # label and make the held-out frame empty-like to test refusal, not accuracy.
    tuner.samples[-1]['current']=tuner.samples[-1]['reference'].copy()
    result=tuner.suggest('A',parameters(tuner))
    assert not result['report']['can_save'] and result['report']['false_empty']==1
    with pytest.raises(ValueError):tuner.apply('A',result['parameters'])


def test_same_frame_cannot_be_counted_as_three_independent_samples(tuning):
    tuner,worker,frame,*_=tuning
    stamp=time.monotonic()
    worker.robot.cameras['front'].read_latest_with_timestamp=lambda **kw:(frame.copy(),stamp)
    row=tuner.capture('A');tuner.label(row['id'],'EMPTY')
    with pytest.raises(ValueError,match='중복'):tuner.capture('A')
    assert len(tuner.samples)==1


def test_failed_audit_write_does_not_claim_label_success(tuning,monkeypatch):
    tuner,*_=tuning;row=tuner.capture('A')
    def fail(*a,**kw):raise OSError('disk full')
    monkeypatch.setattr(np,'savez_compressed',fail)
    with pytest.raises(OSError,match='disk full'):tuner.label(row['id'],'EMPTY')
    assert tuner.samples==[] and tuner.pending is not None
