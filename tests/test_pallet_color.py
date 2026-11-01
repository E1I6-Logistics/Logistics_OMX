"""Opt-in fixed COLOURED product mode, not generic white-object detection."""
import copy
import json
import time
import numpy as np
import cv2
import pytest
from omx_manager import pallet as P,config as C
from test_manager import cfg
from test_pallet_stock import stock,producer
from test_pallet_tuning import tuning,collect,parameters


def scene():
    a=np.full((100,140,3),210,np.uint8)
    for x in range(0,140,12):a[:,x:x+3]=90
    for y in range(0,100,12):a[y:y+4,:]=210
    return cv2.GaussianBlur(a,(3,3),0)


@pytest.mark.parametrize('color',[(160,110,60),(245,230,120),(215,190,155)])
@pytest.mark.parametrize('where',[(40,30),(8,10),(105,60)])
def test_known_colours_small_products_and_return_to_empty(color,where):
    ref=scene();a=ref.copy();x,y=where;a[y:y+14,x:x+14]=color
    s=dict(P.DEFAULTS,color_presence_enabled=True)
    before=a.copy();baseline=ref.copy()
    assert P.compare(ref,ref,s)['candidate']=='EMPTY'
    assert P.compare(a,ref,s)['candidate']=='PRESENT'
    assert np.array_equal(a,before) and np.array_equal(ref,baseline)


@pytest.mark.parametrize('angle',[-5,5])
def test_empty_rotated_or_lighting_changed_stays_empty(angle):
    ref=scene();matrix=cv2.getRotationMatrix2D((70,50),angle,1)
    a=cv2.warpAffine(ref,matrix,(140,100),borderMode=cv2.BORDER_REPLICATE)
    a=np.uint8(np.clip(a.astype(float)*[.85,.95,1.05]+8,0,255))
    row=P.compare(a,ref,dict(P.DEFAULTS,color_presence_enabled=True))
    assert row['candidate']=='EMPTY',row


@pytest.mark.parametrize('value',[0,255,130])
def test_bad_lighting_flat_occlusion_never_empty(value):
    ref=scene();a=np.full_like(ref,value)
    row=P.compare(a,ref,dict(P.DEFAULTS,color_presence_enabled=True))
    assert row['candidate']=='UNKNOWN'


def test_new_mode_is_disabled_by_default_and_neutral_product_needs_rejection(tuning):
    tuner,worker,frame,path,directory=tuning
    assert not P.DEFAULTS['color_presence_enabled']
    collect(tuning,'EMPTY');collect(tuning,'PRESENT') # existing grey product fixture
    args=dict(parameters(tuner),color_presence_enabled=True)
    result=tuner.evaluate('A',args)
    assert not result['can_save'] # grey product cannot be commissioned as coloured


@pytest.mark.parametrize('key,value',[('color_presence_enabled',1),('color_threshold',.01),('color_threshold',float('nan')),('color_min_component_pixels',501),('color_empty_component_pixels',80)])
def test_colour_parameters_are_bounded(stock,key,value):
    cfg,*_=stock;cfg['pallet_stock'][key]=value
    with pytest.raises(C.ConfigError):P.validate(cfg)


def test_historical_import_preserves_reference_and_source_time(tuning):
    tuner,worker,frame,path,directory=tuning
    collect(tuning,'EMPTY');collect(tuning,'PRESENT')
    old=tuner.samples[0];before=(directory/'pallet_refs/A.npz').read_bytes()
    source_time=old['at'];tuner.dispatch({'action':'clear'})
    result=tuner.dispatch({'action':'import','item':'A'})
    assert len(result['samples'])==6
    assert tuner.samples[0]['at']==source_time and tuner.samples[0]['historical']
    assert tuner.evaluate('A',parameters(tuner))['can_save']
    assert (directory/'pallet_refs/A.npz').read_bytes()==before


def test_historical_import_rejects_replaced_reference_and_is_atomic(tuning):
    tuner,worker,frame,path,directory=tuning
    collect(tuning,'EMPTY');before=list(tuner.samples)
    P.save_reference(worker.cfg,'A',[frame+1]*3)
    with pytest.raises(ValueError,match='빈 기준'):tuner.import_samples('A')
    assert tuner.samples==before


def test_historical_import_requires_live_service(tuning):
    tuner,worker,frame,path,directory=tuning;collect(tuning,'EMPTY')
    C.write_json(directory/'pallet_stock.json',dict(at=time.time()-100,session='s'))
    with pytest.raises(ValueError,match='홈 대기'):tuner.import_samples('A')


def test_replay_archives_are_read_only_and_import_can_apply(tuning,tmp_path):
    import importlib.util
    import zipfile
    from pathlib import Path
    tuner,worker,frame,path,directory=tuning
    collect(tuning,'EMPTY');collect(tuning,'PRESENT')
    archive=tmp_path/'samples.zip'
    with zipfile.ZipFile(archive,'w') as z:
        for sample in sorted((directory/'pallet_tuning').glob('*.npz')):z.write(sample,sample.name)
    before=archive.read_bytes()
    script=Path(__file__).resolve().parents[1]/'scripts/evaluate_pallet_tuning.py'
    spec=importlib.util.spec_from_file_location('evaluate_pallet_tuning',script)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    result=mod.evaluate([archive],1)
    assert result['summary']['omx1/A']['baseline']['samples']==6 and archive.read_bytes()==before
    tuner.dispatch({'action':'clear'})
    tuner.import_samples('A')
    args=parameters(tuner)
    assert tuner.evaluate('A',args)['can_save']
    tuner.apply('A',args)
    assert C.load(path)['pallet_stock']['items']['A']['parameters']==args


def test_colour_mode_can_be_saved_and_applied_without_model_changes(tuning):
    tuner,worker,frame,path,directory=tuning
    frame[20:120,20:160]=scene()
    P.save_reference(worker.cfg,'A',[frame]*3) # synthetic test reference only
    original=frame.copy();before=C.load(path);reference=(directory/'pallet_refs/A.npz').read_bytes()
    for label in ('EMPTY','PRESENT'):
        if label=='PRESENT':frame[50:70,60:80]=[205,170,110]
        for _ in range(3):
            row=tuner.capture('A');tuner.label(row['id'],label)
    args=dict(parameters(tuner),color_presence_enabled=True)
    assert tuner.evaluate('A',args)['can_save']
    tuner.apply('A',args)
    updated=C.load(path)
    assert updated['pallet_stock']['items']['A']['parameters']['color_presence_enabled']
    assert (directory/'pallet_refs/A.npz').read_bytes()==reference
    assert all(updated[k]==v for k,v in before.items() if k!='pallet_stock')
    # The next actual worker checks use this mode and still require 3 fresh frames.
    frame[:]=original;worker.observe({j+'.pos':0. for j in C.JOINTS})
    for _ in range(3):row=worker.check()
    assert row['stock']['A']=='EMPTY'
