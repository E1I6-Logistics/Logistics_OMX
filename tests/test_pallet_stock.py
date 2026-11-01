"""Synthetic fixed-pallet evidence and stock freshness; no hardware/broker."""
import copy,json,time,os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import numpy as np
import pytest
from omx_manager import config as C,pallet as P,pallet_runtime as W
from omx_manager.pallet_cli import invoke
from test_manager import cfg,commissioned
from test_runtime_reliability import load_function


def image(item=False,bright=False):
    frame=np.full((240,320,3),170,np.uint8)
    for x in range(20,160,12):frame[20:120,x:x+2]=100
    for y in range(20,120,12):frame[y:y+2,20:160]=100
    if item:frame[50:70,60:80]=245 if bright else 60
    return frame


@pytest.fixture
def stock(cfg,tmp_path,monkeypatch):
    monkeypatch.setattr(P,'ROOT',tmp_path)
    cfg['hardware']['cameras']['front'].update(width=320,height=240)
    cfg['pallet_stock']=dict(P.DEFAULTS,enabled=True,items={'A':dict(roi=[20,20,140,100]),'B':dict(roi=[180,20,120,100])})
    frame=image();P.save_reference(cfg,'A',[frame]*3)
    # The second ROI also needs a real textured reference.
    frame[:,180:300]=frame[:,20:140];P.save_reference(cfg,'B',[frame]*3)
    directory=P.directory(cfg);C.write_json(directory/'inventory.json',dict(remaining=0,order_started_at_epoch=10))
    return cfg,frame,directory


@pytest.mark.parametrize('bright',[False,True])
def test_empty_occupied_empty_never_reuses_old_confirmed_state(stock,bright):
    cfg,frame,path=stock;i=P.Inspector(cfg)
    for n in range(3):rows=i.update(frame,100+n*5,100+n*5)
    assert rows['A']['state']=='EMPTY'
    loaded=frame.copy();loaded[50:70,60:80]=245 if bright else 60
    assert i.update(loaded,115,115)['A']['state']=='UNKNOWN'
    i.update(loaded,120,120);assert i.update(loaded,125,125)['A']['state']=='PRESENT'
    assert i.update(frame,130,130)['A']['state']=='UNKNOWN'
    i.update(frame,135,135);assert i.update(frame,140,140)['A']['state']=='EMPTY'


def test_stale_duplicate_missing_reference_and_changed_roi_never_report_empty(stock):
    cfg,frame,path=stock;i=P.Inspector(cfg)
    for n in range(3):i.update(frame,100+n*5,100+n*5)
    assert i.update(frame,110,115)['A']['state']=='UNKNOWN'
    i.update(frame,120,120);assert i.update(frame,120,120)['A']['state']=='UNKNOWN'
    (path/'pallet_refs/A.npz').unlink()
    assert i.update(frame,125,125)['A']['state']=='UNKNOWN'
    cfg['pallet_stock']['items']['B']['roi']=[181,20,119,100]
    assert i.update(frame,130,130)['B']['state']=='UNKNOWN'


def test_lighting_shift_and_overexposed_view_are_unknown(stock):
    cfg,frame,path=stock;i=P.Inspector(cfg)
    shifted=np.clip(frame.astype(int)+20,0,255).astype(np.uint8)
    assert i.update(shifted,10,10)['A']['candidate']=='UNKNOWN'
    assert i.update(np.full_like(frame,255),15,15)['A']['candidate']=='UNKNOWN'


def test_capture_requires_stable_operator_empty_reference_and_bound_identity(stock):
    cfg,frame,path=stock
    with pytest.raises(C.ConfigError):P.save_reference(cfg,'A',[frame,np.zeros_like(frame),frame])
    before=copy.deepcopy(cfg);P.save_reference(cfg,'A',[frame]*3);assert cfg==before
    cfg['hardware']['cameras']['front']['width']=800
    with pytest.raises(C.ConfigError):P.load_reference(cfg,'A')


def producer(stock):
    cfg,frame,path=stock
    robot=NS(_omx_runtime_ready=True,cameras={})
    w=W.Worker(robot,cfg);w.session='s';w.observe({j+'.pos':0. for j in C.JOINTS});w.gate()
    robot.cameras['front']=NS(read_latest_with_timestamp=lambda **kw:(frame.copy(),time.monotonic()))
    return w


def test_worker_only_uses_cached_camera_and_existing_pose_and_blocks_active_order(stock):
    cfg,frame,path=stock;w=producer(stock);w.gate()
    for _ in range(3):row=w.check()
    assert row['stock']['A']=='EMPTY' and row['exact_item_count'] is None
    C.write_json(path/'inventory.json',dict(remaining=1,order_started_at_epoch=11))
    assert set(w.check()['stock'].values())=={'UNKNOWN'}
    C.write_json(path/'inventory.json',dict(remaining=0,order_started_at_epoch=11))
    assert w.check()['stock']['A']=='UNKNOWN'
    w.observe({j+'.pos':50. for j in C.JOINTS});assert w.check()['stock']['A']=='UNKNOWN'


def test_camera_or_worker_failure_is_unknown_and_cleanup_stops_it(stock):
    cfg,frame,path=stock;w=producer(stock)
    w.robot.cameras['front']._omx_exposure_blocked=True
    with pytest.raises(RuntimeError):w.check()
    w.close();assert json.loads((path/'pallet_stock.json').read_text())['stock']['A']=='UNKNOWN'
    disabled=copy.deepcopy(cfg);disabled['pallet_stock']['enabled']=False
    robot=NS();W.attach(robot,disabled);assert not hasattr(robot,'_omx_stock')


def test_job_start_during_image_processing_invalidates_result(stock,monkeypatch):
    cfg,frame,path=stock;w=producer(stock);w.gate()
    def change(*args):
        C.write_json(path/'inventory.json',dict(remaining=0,order_started_at_epoch=99))
        return {k:dict(state='EMPTY') for k in ('A','B')}
    monkeypatch.setattr(w.inspector,'update',change)
    assert w.check()['stock']['A']=='UNKNOWN'


def test_mqtt_never_accepts_old_session_old_pid_old_order_or_expired_empty(stock):
    cfg,frame,path=stock;w=producer(stock);w.gate()
    for _ in range(3):w.check()
    valid=lambda **kw:W.mqtt_payload(cfg,path/'pallet_stock.json','s',os.getpid(),**kw)
    assert valid()['stock']['A']=='EMPTY'
    assert valid(idle=False)['stock']['A']=='UNKNOWN'
    assert valid(now=time.time()+30)['stock']['A']=='UNKNOWN'
    assert W.mqtt_payload(cfg,path/'pallet_stock.json','old',os.getpid())['stock']['A']=='UNKNOWN'
    assert W.mqtt_payload(cfg,path/'pallet_stock.json','s',-1)['stock']['A']=='UNKNOWN'
    C.write_json(path/'inventory.json',dict(remaining=0,order_started_at_epoch=12))
    assert valid()['stock']['A']=='UNKNOWN'


def test_snapshot_and_empty_capture_request_use_same_owner_no_new_camera(stock):
    cfg,frame,path=stock;w=producer(stock);w.gate()
    C.write_json(path/'pallet_request.json',dict(token='a'*32,session='s',at=time.time(),action='snapshot'))
    w.command();reply=json.loads((path/'pallet_reply.json').read_text())
    assert reply['ok'] and (path/'omx1_pallet_view.png').exists() and reply['device']=='omx1'
    C.write_json(path/'pallet_request.json',dict(token='b'*32,session='old',at=time.time(),action='capture',item='A',confirm_empty=True))
    before=(path/'pallet_refs/A.npz').read_bytes();w.command()
    assert not json.loads((path/'pallet_reply.json').read_text())['ok'] and (path/'pallet_refs/A.npz').read_bytes()==before


def test_valid_empty_capture_is_bound_and_requires_confirmation(stock):
    cfg,frame,path=stock;w=producer(stock);w.gate()
    C.write_json(path/'pallet_request.json',dict(token='a'*32,session='s',at=time.time(),action='capture',item='A',confirm_empty=True))
    w.command();assert json.loads((path/'pallet_reply.json').read_text())['ok']
    with pytest.raises(ValueError,match='완전히 비운'):
        config_path=path/'config.yaml';C.save(config_path,cfg);invoke(config_path,'capture','A')


def test_worker_shutdown_precedes_camera_cleanup_and_thread_exits(stock):
    from omx_manager.hardware import cleanup_resources
    cfg,frame,path=stock;w=producer(stock);w.start();w.close()
    assert not w.thread.is_alive()
    events=[];robot=NS(_omx_stock=NS(close=lambda:events.append('stock')),cameras={'front':NS(disconnect=lambda:events.append('camera'))})
    assert cleanup_resources(robot=robot)==[] and events==['stock','camera']


@pytest.mark.parametrize('cfg',['omx1','omx2'],indirect=True)
def test_cli_setup_and_roi_do_not_change_models_home_or_calibration(cfg,tmp_path,monkeypatch):
    monkeypatch.setattr(P,'ROOT',tmp_path)
    import omx_manager.pallet_cli as cli
    from contextlib import nullcontext
    monkeypatch.setattr(cli,'device_lock',lambda _:nullcontext())
    path=tmp_path/'cfg.yaml';C.save(path,cfg);before=copy.deepcopy(cfg)
    invoke(path,'setup');invoke(path,'roi',P.ITEMS[cfg['device_id']][0],'[20,20,100,100]');invoke(path,'on')
    after=C.load(path);assert after['pallet_stock']['enabled']
    assert all(before[k]==after[k] for k in ('hardware','home','models','counting'))


@pytest.mark.parametrize('field,value',[('interval_s',.1),('stable_samples',True),('camera',[]),('pixel_threshold',False),('empty_ratio',.3)])
def test_bad_config_rejected(stock,field,value):
    cfg,*_=stock;cfg['pallet_stock'][field]=value
    with pytest.raises(C.ConfigError):C.validate(cfg)


def test_wrong_robot_or_out_of_frame_roi_rejected(stock):
    cfg,*_=stock;cfg['pallet_stock']['items']['A']['roi']=[600,0,100,100]
    with pytest.raises(C.ConfigError):C.validate(cfg)
    cfg['device_id']='omx3';cfg['role']='unload'
    with pytest.raises(C.ConfigError):P.validate(cfg)


def test_publish_is_dedicated_nonretained_and_does_not_modify_order_state(stock,monkeypatch):
    cfg,frame,path=stock;w=producer(stock);w.gate()
    for _ in range(3):w.check()
    monkeypatch.setenv('OMX_SESSION','s');monkeypatch.setenv('OMX_ROLLOUT_PID',str(os.getpid()))
    client=NS(publish=Mock());before=(path/'inventory.json').read_bytes()
    fn=load_function('lerobot_unified/omx_mqtt_client.py','send_pallet_stock',namespace=dict(state_path=path/'inventory.json',robot_id='omx1',client=client,
                    os=os,json=json,current_state='IDLE',current_job_id=None))
    fn(cfg);args=client.publish.call_args
    assert args.args[0]=='omx1/stock' and args.kwargs==dict(qos=0,retain=False)
    assert json.loads(args.args[1])['stock']['A']=='EMPTY' and (path/'inventory.json').read_bytes()==before


def test_dead_or_reused_producer_pid_invalidates_even_fresh_empty(stock,monkeypatch):
    cfg,frame,path=stock;w=producer(stock);w.gate()
    for _ in range(3):w.check()
    monkeypatch.setattr(W,'process_start',lambda _:None)
    assert W.mqtt_payload(cfg,path/'pallet_stock.json','s',os.getpid())['stock']['A']=='UNKNOWN'
    monkeypatch.setattr(W,'process_start',lambda _:999999)
    assert W.mqtt_payload(cfg,path/'pallet_stock.json','s',os.getpid())['stock']['A']=='UNKNOWN'


@pytest.mark.parametrize('offset',[(1,-2,-7),(5,3,-2),(-10,3,2),(18,18,18)])
@pytest.mark.parametrize('occupied',[False,True])
def test_bounded_channel_shift_detects_present_then_empty_without_recapture(stock,offset,occupied):
    cfg,frame,path=stock
    reference=(path/'pallet_refs/A.npz').read_bytes();source=frame.copy()
    # Match the field A channel shift; the presence case has a local brown box.
    if occupied:source[48:74,56:84]=[120,80,45]
    shifted=np.clip(source.astype(int)+np.array(offset),0,255).astype(np.uint8)
    original=shifted.copy();inspector=P.Inspector(cfg)
    for n in range(3):row=inspector.update(shifted,100+5*n,100+5*n)['A']
    assert row['state']==('PRESENT' if occupied else 'EMPTY')
    assert row['features_computed'] and row['quality_flags']==[]
    assert row['color_offset_limit']==18 and row['stable_samples']==3
    assert np.allclose(row['color_offset'],offset,atol=1)
    assert np.array_equal(shifted,original) and (path/'pallet_refs/A.npz').read_bytes()==reference
    if occupied:
        empty=np.clip(frame.astype(int)+np.array(offset),0,255).astype(np.uint8)
        assert inspector.update(empty,115,115)['A']['state']=='UNKNOWN'
        inspector.update(empty,120,120)
        assert inspector.update(empty,125,125)['A']['state']=='EMPTY'


def test_explicit_old_color_limit_is_respected_and_causes_are_separate(stock):
    cfg,frame,path=stock;cfg['pallet_stock']['max_color_offset']=6.
    changed=np.clip(frame.astype(int)+np.array([1,-2,-7]),0,255).astype(np.uint8)
    row=P.Inspector(cfg).update(changed,100,100)['A']
    assert row['candidate']=='UNKNOWN' and row['quality_flags']==['COLOR_OFFSET_LIMIT']
    assert not row['features_computed'] and row['clipped_ratio']==0
    assert row['color_offset_limit']==6
    white=P.Inspector(cfg).update(np.full_like(frame,255),100,100)['A']
    assert 'OVEREXPOSED' in white['quality_flags'] and white['clipped_ratio']==1
    assert white['reason']=='과노출 픽셀 비율 증가'


@pytest.mark.parametrize('offset',[19,30,-25])
def test_large_shift_stays_unknown_even_with_object(stock,offset):
    cfg,frame,path=stock;frame=frame.copy();frame[50:70,60:80]=60
    shifted=np.clip(frame.astype(int)+offset,0,255).astype(np.uint8)
    inspector=P.Inspector(cfg)
    for n in range(5):row=inspector.update(shifted,100+5*n,100+5*n)['A']
    assert row['state']=='UNKNOWN' and row['quality_flags']==['COLOR_OFFSET_LIMIT']
    assert row['stable_samples']==0 and not row['features_computed']


@pytest.mark.parametrize('value',[-1,31,True,float('nan')])
def test_color_limit_validated_in_yaml(stock,value):
    cfg,*_=stock;cfg['pallet_stock']['max_color_offset']=value
    with pytest.raises(C.ConfigError):C.validate(cfg)


def test_color_limit_edit_preserves_reference_signature_and_normal_confirmation(stock):
    cfg,frame,path=stock
    from omx_manager.cli import edit_value
    before=P.signature(cfg,'A');old=(path/'pallet_refs/A.npz').read_bytes()
    new=edit_value(cfg,'pallet_stock.max_color_offset','12')
    assert P.signature(new,'A')==before and P.load_reference(new,'A').ndim==3
    assert new['home']==cfg['home'] and new['models']==cfg['models']
    assert (path/'pallet_refs/A.npz').read_bytes()==old


def test_moderate_color_shift_with_sensor_noise_does_not_invent_object(stock):
    cfg,frame,path=stock;inspector=P.Inspector(cfg)
    rng=np.random.default_rng(17)
    for n in range(5):
        noise=rng.integers(-2,3,frame.shape)
        changed=np.clip(frame.astype(int)+[1,-2,-7]+noise,0,255).astype(np.uint8)
        row=inspector.update(changed,100+5*n,100+5*n)['A']
        assert row['candidate']=='EMPTY'
    assert row['state']=='EMPTY'
