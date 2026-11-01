"""Four fixed-product pallets under light, small shifts and sensor noise."""
import copy
import json
import time

import cv2
import numpy as np
import pytest

from omx_manager import pallet as P, pallet_runtime as W, config as C
from omx_manager.pallet_cli import invoke
from test_pallet_stock import stock, producer
from test_manager import cfg


def sample(cfg, frame, *, offset=(0,0,0), shift=(0,0), product=None, noise=0):
    reference=P.crop(frame,cfg['pallet_stock']['items']['A']['roi'])
    current=reference.copy()
    if product is not None:
        x,y,w,h,color=product
        current[y:y+h,x:x+w]=color
        current=cv2.GaussianBlur(current,(3,3),0)
    if shift!=(0,0):
        current=cv2.warpAffine(current,np.float32([[1,0,shift[0]],[0,1,shift[1]]]),
            (current.shape[1],current.shape[0]),borderMode=cv2.BORDER_REPLICATE)
    extra=np.random.default_rng(5).integers(-noise,noise+1,current.shape) if noise else 0
    current=np.uint8(np.clip(current.astype(int)+offset+extra,0,255))
    return current,reference


def spec(cfg):return dict(P.options(cfg),precision_enabled=True)


@pytest.mark.parametrize('offset',[(-11,-18,-25),(25,20,15),(-25,-10,0),(1,-2,-7)])
@pytest.mark.parametrize('shift',[(0,0),(2,-2),(-3,2)])
def test_empty_remains_empty_through_lighting_shift_and_noise(stock,offset,shift):
    cfg,frame,_=stock
    current,reference=sample(cfg,frame,offset=offset,shift=shift,noise=2)
    before=current.copy();reference_before=reference.copy()
    row=P.compare(current,reference,spec(cfg))
    assert row['candidate']=='EMPTY',row
    assert row['quality_flags']==[]
    assert np.array_equal(before,current) and np.array_equal(reference_before,reference)
    if max(abs(x) for x in offset)>18:
        assert row['lighting_method']=='TEXTURED_BACKGROUND' and row['lighting_anchor_tiles']>=6


@pytest.mark.parametrize('color',[(120,80,45),(245,235,160),(230,215,165),(215,210,205)])
@pytest.mark.parametrize('where',[(35,30,20,20),(8,7,12,12),(115,75,12,12)])
def test_each_product_present_and_empty_again(stock,color,where):
    cfg,frame,_=stock
    current,reference=sample(cfg,frame,product=(*where,color),offset=(-11,-18,-25),shift=(2,-1),noise=2)
    row=P.compare(current,reference,spec(cfg))
    assert row['candidate']=='PRESENT',row
    empty,_=sample(cfg,frame,offset=(-11,-18,-25),shift=(2,-1),noise=2)
    assert P.compare(empty,reference,spec(cfg))['candidate']=='EMPTY'


def test_small_two_centimeter_like_object_is_not_erased(stock):
    cfg,frame,_=stock
    current,reference=sample(cfg,frame,product=(55,35,8,8,(245,235,170)),offset=(-11,-18,-25))
    row=P.compare(current,reference,spec(cfg))
    assert row['candidate']=='PRESENT',row


def test_products_covering_much_of_grid_are_never_compensated_to_empty(stock):
    cfg,frame,_=stock
    current,reference=sample(cfg,frame,offset=(-11,-18,-25))
    for x in (12,72):
        for y in (8,55):current[y:y+32,x:x+42]=[90,60,40]
    row=P.compare(current,reference,spec(cfg))
    assert row['candidate'] in ('PRESENT','UNKNOWN') and row['candidate']!='EMPTY'


def test_missing_background_extreme_shift_and_clipping_remain_unknown(stock):
    cfg,frame,_=stock
    _,reference=sample(cfg,frame)
    for current in (np.full_like(reference,80),np.full_like(reference,255),np.uint8(np.clip(reference.astype(int)+65,0,255))):
        row=P.compare(current,reference,spec(cfg))
        assert row['candidate']=='UNKNOWN',row


def test_smooth_local_light_change_is_not_an_object(stock):
    cfg,frame,_=stock
    current,reference=sample(cfg,frame)
    yy,xx=np.mgrid[:current.shape[0],:current.shape[1]]
    field=np.stack([5*xx/current.shape[1]-22,4*yy/current.shape[0]-22,4*xx/current.shape[1]-27],axis=2)
    current=np.uint8(np.clip(current.astype(float)+field,0,255))
    row=P.compare(current,reference,spec(cfg))
    assert row['candidate']=='EMPTY',row


def test_improve_and_legacy_preserve_models_home_and_reference(stock,monkeypatch):
    from contextlib import nullcontext
    from omx_manager import pallet_cli
    cfg,frame,directory=stock
    monkeypatch.setattr(pallet_cli,'device_lock',lambda _:nullcontext())
    path=directory/'config.yaml';C.save(path,cfg)
    before=copy.deepcopy(cfg);reference=(directory/'pallet_refs/A.npz').read_bytes();binding=P.signature(cfg,'A')
    invoke(path,'improve');updated=C.load(path)
    assert updated['pallet_stock']['precision_enabled']
    assert all(updated[key]==before[key] for key in ('hardware','models','home','counting','workflow','recovery'))
    assert P.signature(updated,'A')==binding and (directory/'pallet_refs/A.npz').read_bytes()==reference
    invoke(path,'legacy');assert not C.load(path)['pallet_stock']['precision_enabled']


@pytest.mark.parametrize('field,value',[('precision_enabled',1),('adaptive_color_limit',61),('alignment_max_px',6),('reference_slack_px',4),('min_object_width_px',2)])
def test_new_parameters_are_bounded(stock,field,value):
    cfg,_,_=stock;cfg['pallet_stock'][field]=value
    with pytest.raises(C.ConfigError):P.validate(cfg)


def test_diagnose_keeps_references_counts_and_model_state_unchanged(stock):
    cfg,frame,directory=stock
    worker=producer(stock);worker.gate()
    before=(directory/'pallet_refs/A.npz').read_bytes();inventory=(directory/'inventory.json').read_bytes()
    cfg_before=copy.deepcopy(cfg)
    C.write_json(directory/'pallet_request.json',dict(action='diagnose',item='A',session='s',token='a'*32,at=time.time()))
    worker.command();reply=json.loads((directory/'pallet_reply.json').read_text())
    assert reply['ok'] and 'comparison' in reply
    assert (directory/'omx1_pallet_A_diagnose.png').exists()
    with np.load(directory/'omx1_pallet_A_evidence.npz',allow_pickle=False) as evidence:
        assert evidence['current'].shape==evidence['reference'].shape
    assert (directory/'pallet_refs/A.npz').read_bytes()==before
    assert (directory/'inventory.json').read_bytes()==inventory and cfg==cfg_before


@pytest.mark.parametrize('angle',[-1.,1.])
def test_small_rotation_of_empty_grid_is_not_an_object(stock,angle):
    cfg,frame,_=stock
    current,reference=sample(cfg,frame,offset=(-11,-18,-25))
    height,width=current.shape[:2]
    current=cv2.warpAffine(current,cv2.getRotationMatrix2D((width/2,height/2),angle,1.),
                           (width,height),borderMode=cv2.BORDER_REPLICATE)
    row=P.compare(current,reference,spec(cfg))
    assert row['candidate']=='EMPTY',row


def test_confirmation_does_not_reuse_old_empty_or_present(stock):
    cfg,frame,directory=stock
    cfg['pallet_stock']['precision_enabled']=True
    inspector=P.Inspector(cfg)
    for n in range(3):assert inspector.update(frame,100+5*n,100+5*n)['A']['candidate']=='EMPTY'
    occupied=frame.copy();occupied[50:70,60:80]=[120,80,45]
    assert inspector.update(occupied,115,115)['A']['state']=='UNKNOWN'
    inspector.update(occupied,120,120)
    assert inspector.update(occupied,125,125)['A']['state']=='PRESENT'
    assert inspector.update(frame,130,130)['A']['state']=='UNKNOWN'
    inspector.update(frame,135,135)
    assert inspector.update(frame,140,140)['A']['state']=='EMPTY'


def test_diagnostics_request_rejected_while_order_active(stock):
    cfg,frame,directory=stock
    worker=producer(stock);worker.gate()
    C.write_json(directory/'inventory.json',dict(remaining=1,order_started_at_epoch=11))
    C.write_json(directory/'pallet_request.json',dict(action='diagnose',item='A',session='s',token='b'*32,at=time.time()))
    worker.command()
    assert not json.loads((directory/'pallet_reply.json').read_text())['ok']
    assert not (directory/'omx1_pallet_A_evidence.npz').exists()


def test_saved_evidence_replays_both_modes_without_modification(stock):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'scripts/replay_pallet_evidence.py'
    module=importlib.util.module_from_spec(importlib.util.spec_from_file_location('replay_pallet_evidence',path))
    module.__spec__.loader.exec_module(module)
    cfg,frame,directory=stock
    current,reference=sample(cfg,frame,product=(35,30,20,20,(120,80,45)),offset=(-11,-18,-25))
    evidence=directory/'evidence.npz'
    np.savez_compressed(evidence,current=current,reference=reference,options=np.array(json.dumps(P.options(cfg))),
                        robot_id=np.array('omx1'),item=np.array('A'))
    before=evidence.read_bytes();result=module.replay(evidence,repeats=1)
    assert result['legacy']['candidate']=='UNKNOWN'
    assert result['precision']['candidate']=='PRESENT'
    assert evidence.read_bytes()==before


@pytest.mark.parametrize('color',[215,220,225,230,235])
def test_pale_product_hiding_white_lattice_is_not_called_empty(stock,color):
    cfg,_,_=stock
    reference=np.full((100,140,3),235,np.uint8)
    for x in range(0,140,12):reference[:,x:x+2]=100
    for y in range(0,100,12):reference[y:y+2,:]=100
    reference=cv2.GaussianBlur(reference,(3,3),0)
    current=reference.copy();current[35:47,55:67]=color
    current=cv2.GaussianBlur(current,(3,3),0)
    row=P.compare(current,reference,spec(cfg))
    assert row['candidate']=='PRESENT',row
    assert row['grid_occlusion_pixels']>=20
    assert P.compare(reference,reference,spec(cfg))['candidate']=='EMPTY'


def test_whole_view_blur_is_not_claimed_empty(stock):
    cfg,_,_=stock
    reference=np.full((100,140,3),235,np.uint8)
    for x in range(0,140,12):reference[:,x:x+2]=100
    for y in range(0,100,12):reference[y:y+2,:]=100
    reference=cv2.GaussianBlur(reference,(3,3),0)
    blurred=cv2.GaussianBlur(reference,(15,15),0)
    row=P.compare(blurred,reference,spec(cfg))
    assert row['candidate']=='UNKNOWN' and row['quality_flags']==['TEXTURE_LOST']
