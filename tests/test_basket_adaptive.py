from contextlib import nullcontext
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
import cv2
import numpy as np
import pytest
from omx_manager import basket_registration as R,exposure as E
from omx_manager.basket_motion import observe
from test_basket_registration import spec,partial_spec,scene
from test_low_exposure import setup


def tray():
    ref=np.full((128,128,3),140,np.uint8)
    ref[12:17,:]=100;ref[:,9:14]=100
    current=np.full_like(ref,145);current[16:21,:]=105;current[:,13:18]=105
    return ref,current


@pytest.mark.parametrize('partial',[False,True])
def test_shifted_thin_wall_stripes_cannot_become_an_object(partial):
    s=partial_spec() if partial else spec();ref,current=tray()
    result=R.Comparator(ref,s).compare(current)
    assert result['verdict']=='EMPTY' and result['component_pixels']==0


@pytest.mark.parametrize('where',[(50,50),(7,40),(102,90)])
@pytest.mark.parametrize('color',[(105,105,105),(160,160,150),(100,70,40)])
def test_two_cm_object_remains_detectable_even_next_to_a_wall(where,color):
    ref=np.full((128,128,3),140,np.uint8);current=ref.copy();x,y=where
    current[y:y+14,x:x+14]=color
    assert R.Comparator(ref,partial_spec()).compare(current)['verdict']=='OCCUPIED'


def test_spatial_freedom_cannot_erase_faint_objects_on_sloped_plain_floor():
    line=np.linspace(80,160,128).astype(np.uint8)
    ref=np.repeat(np.tile(line,(128,1))[:,:,None],3,2);current=ref.copy()
    current[48:68,48:68]-=7
    assert R.Comparator(ref,spec()).compare(current)['verdict']=='UNKNOWN'


def test_distributed_background_allows_moderate_lighting_without_rim_consensus():
    ref,current=tray();current=np.clip(current.astype(np.int16)+[3,0,-1],0,255).astype(np.uint8)
    result=R.Comparator(ref,spec()).compare(current)
    assert result['background_consensus'] and result['background_tiles']>=8
    assert result['verdict']=='EMPTY'


@pytest.mark.parametrize('value',[0,255])
def test_adaptation_does_not_override_bad_exposure(value):
    ref=np.full((128,128,3),120,np.uint8);current=np.full_like(ref,value)
    assert R.Comparator(ref,spec()).compare(current)['verdict']=='UNKNOWN'


def test_unobservable_noisy_background_is_not_empty():
    ref=np.full((128,128,3),120,np.uint8)
    current=np.clip(ref.astype(np.int16)+np.random.default_rng(5).integers(-20,21,ref.shape),0,255).astype(np.uint8)
    result=R.Comparator(ref,spec()).compare(current)
    assert result['verdict']=='UNKNOWN' and 'BACKGROUND_UNOBSERVABLE' in result['quality_flags']


def test_virtual_corner_jitter_is_not_motion_when_observed_rim_is_unchanged():
    image,q=scene();gray=cv2.GaussianBlur(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),(5,5),0)
    tracker=NS(spec=spec());observe(tracker,gray,q,[0,0]);jitter=q.copy();jitter[0]+=[0,8]
    result=observe(tracker,gray,jitter,[0,0])
    assert result['observed_rim_stable'] is True and result['rim_motion_delta']==0


def test_actual_camera_rim_motion_is_still_rejected():
    image,q=scene();gray=cv2.GaussianBlur(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),(5,5),0)
    tracker=NS(spec=spec());observe(tracker,gray,q,[0,0])
    moved=cv2.warpAffine(gray,np.float32([[1,0,5],[0,1,0]]),(640,480))
    assert observe(tracker,moved,q+[5,0],[0,0])['observed_rim_stable'] is False


def focus_adapter(controls):
    values={cv2.CAP_PROP_AUTOFOCUS:1.,cv2.CAP_PROP_FOCUS:80.};writes=[]
    def set_value(key,value):values[key]=value;writes.append((key,value));return True
    controls.cap=NS(get=lambda key:values[key],set=set_value)
    controls.access=lambda:nullcontext()
    return values,writes


def test_inspection_focus_freezes_and_restores_without_setting_manual_position(setup):
    cfg,clock,controls,camera=setup;focus,writes=focus_adapter(controls)
    with E.Session(camera,cfg) as transaction:
        assert focus[cv2.CAP_PROP_AUTOFOCUS]==0
        assert E.journal_path(cfg).exists();transaction.verify()
    assert focus[cv2.CAP_PROP_AUTOFOCUS]==1
    assert all(key==cv2.CAP_PROP_AUTOFOCUS for key,_ in writes)
    row=json.loads(E.journal_path(cfg).with_name('camera_exposure_last.json').read_text())
    assert row['focus_original']['auto_focus']==1 and row['focus_locked']['auto_focus']==0


@pytest.mark.parametrize('error',[RuntimeError,KeyboardInterrupt])
def test_focus_is_restored_after_failure_or_operator_interrupt(setup,error):
    cfg,clock,controls,camera=setup;focus,_=focus_adapter(controls)
    with pytest.raises(error):
        with E.Session(camera,cfg):raise error('stop')
    assert focus[cv2.CAP_PROP_AUTOFOCUS]==1 and not E.journal_path(cfg).exists()


def test_focus_restore_failure_leaves_durable_recovery_fence(setup):
    cfg,clock,controls,camera=setup;focus,_=focus_adapter(controls);set_value=controls.cap.set
    controls.cap.set=lambda key,value:False if value==1 else set_value(key,value)
    with pytest.raises(RuntimeError,match='restoration'):
        with E.Session(camera,cfg):pass
    assert E.journal_path(cfg).exists() and camera._omx_exposure_blocked
    controls.cap.set=set_value;E.recover(camera,cfg)
    assert focus[cv2.CAP_PROP_AUTOFOCUS]==1 and not E.journal_path(cfg).exists()


def test_fixed_manual_focus_is_not_modified(setup):
    cfg,clock,controls,camera=setup;focus,writes=focus_adapter(controls);focus[cv2.CAP_PROP_AUTOFOCUS]=0.
    with E.Session(camera,cfg):pass
    assert writes==[] and focus[cv2.CAP_PROP_AUTOFOCUS]==0


@pytest.mark.parametrize('shift,verdict',[(17,'EMPTY'),(24,'EMPTY'),(25,'UNKNOWN')])
def test_verified_background_adaptation_has_a_hard_lighting_bound(shift,verdict):
    ref=np.full((128,128,3),140,np.uint8)
    current=np.full_like(ref,140-shift)
    row=R.Comparator(ref,spec()).compare(current)
    assert row['verdict']==verdict and row['background_consensus']
    assert row['lighting_offset_source']=='background_consensus'
    if verdict=='UNKNOWN':assert row['quality_flags']==['LIGHTING_SHIFT']


def test_cargo_tiles_do_not_pull_distributed_background_to_the_wrong_mode():
    ref=np.full((128,128,3),140,np.uint8);current=np.full_like(ref,123)
    current[32:80,32:80]=[100,70,40]
    row=R.Comparator(ref,spec()).compare(current)
    assert row['background_consensus'] and row['color_offset']==[-17.,-17.,-17.]
    assert row['verdict']=='OCCUPIED'


def test_spatially_inconsistent_lighting_cannot_authorize_color_fallback():
    ref=np.full((128,128,3),140,np.uint8);current=ref.copy()
    for y in range(4):
        for x in range(4):current[y*32:(y+1)*32,x*32:(x+1)*32]=[100+4*x,100+4*y,100+4*(x+y)]
    current[50:70,50:70]=[90,60,40]
    row=R.Comparator(ref,spec()).compare(current)
    assert not row['background_consensus'] and row['verdict']=='UNKNOWN'


def test_exposure_restore_failure_still_attempts_original_autofocus(setup,monkeypatch):
    cfg,clock,controls,camera=setup;focus,_=focus_adapter(controls)
    with pytest.raises(RuntimeError,match='restoration'):
        with E.Session(camera,cfg):
            monkeypatch.setattr(E,'restore_controls',Mock(side_effect=RuntimeError('sensor failure')))
    assert focus[cv2.CAP_PROP_AUTOFOCUS]==1
    assert E.journal_path(cfg).exists() and camera._omx_exposure_blocked
