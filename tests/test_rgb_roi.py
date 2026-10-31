"""Synthetic RGB inspection only; no robot, camera, depth, or model execution."""
import copy
from pathlib import Path

import cv2
import numpy as np
import pytest

from omx_manager import config as C
from omx_manager.basket import BasketInspector,crop,options
from omx_manager.commissioning import tune
from omx_manager.rgb_roi import Comparator,SIZE,signature,draw_region
from omx_manager.workflow import registration_binding

ROOT=Path(__file__).resolve().parents[1]


def empty(value=230):return np.full((480,640,3),value,np.uint8)


def configured(tmp_path,reference=None,**settings):
    cfg=tune(C.load(ROOT/'params/omx4.yaml'))
    cfg['home']['target']={j:0. for j in C.JOINTS};cfg['home']['target']['gripper']=50
    cfg['unload'].update(roi=[0,0,640,480],full_view_confirmed=True,**settings)
    spec=options(cfg);path=tmp_path/'reference.npz'
    image=empty() if reference is None else reference
    np.savez_compressed(path,image=crop(image,spec),roi=spec['roi'],roi_signature=signature(spec))
    cfg['unload'].update(reference=str(path),reference_binding=registration_binding(cfg))
    return cfg


def verdict(cfg,image):
    inspector=BasketInspector(cfg)
    for i in range(5):result=inspector.update(image,1+i*.11,1+i*.11)
    return result,inspector


@pytest.mark.parametrize('value',[100,200,230])
def test_visible_unchanged_reference_is_empty(tmp_path,value):
    cfg=configured(tmp_path,empty(value))
    assert verdict(cfg,empty(value))[0]=='EMPTY'


@pytest.mark.parametrize('place',[(50,50),(300,200),(570,410)])
@pytest.mark.parametrize('tone',[220,240])
def test_small_bright_objects_under_global_empty_ratio_are_detected(tmp_path,place,tone):
    cfg=configured(tmp_path);image=empty();x,y=place
    image[y:y+14,x:x+14]=tone
    result,inspector=verdict(cfg,image)
    assert inspector.ratio<cfg['unload']['empty_ratio']
    assert result=='OCCUPIED',inspector.snapshot(1)


def test_removal_clears_presence_and_uses_fresh_hold(tmp_path):
    cfg=configured(tmp_path);image=empty();image[100:118,100:118]=220
    result,inspector=verdict(cfg,image);assert result=='OCCUPIED'
    assert inspector.update(empty(),1.55,1.55)=='UNKNOWN'
    assert inspector.update(empty(),1.66,1.66)=='UNKNOWN'
    assert inspector.update(empty(),1.77,1.77)=='EMPTY'


def test_overexposed_and_unconfirmed_views_cannot_be_empty(tmp_path):
    cfg=configured(tmp_path,empty(255))
    result,inspector=verdict(cfg,empty(255))
    assert result=='UNKNOWN' and inspector.reason=='ROI_OVEREXPOSED'
    cfg=configured(tmp_path);cfg['unload']['full_view_confirmed']=False
    result,inspector=verdict(cfg,empty())
    assert result=='UNKNOWN' and inspector.reason=='FULL_VIEW_NOT_CONFIRMED'


def test_one_faint_spot_blocks_false_empty_even_when_not_enough_for_presence(tmp_path):
    cfg=configured(tmp_path);image=empty();image[200:204,200:204]=220
    result,inspector=verdict(cfg,image)
    assert result!='EMPTY',inspector.snapshot(1)


def test_polygon_excludes_outside_background_but_keeps_internal_object(tmp_path):
    polygon=[[160,80],[480,80],[480,400],[160,400]]
    cfg=configured(tmp_path,roi_polygon=polygon)
    image=empty();image[:,:120]=30
    assert verdict(cfg,image)[0]=='EMPTY'
    image[200:216,300:316]=220
    assert verdict(cfg,image)[0]=='OCCUPIED'


def test_large_illumination_change_is_distinct_unknown(tmp_path):
    cfg=configured(tmp_path);result,inspector=verdict(cfg,empty(190))
    assert result=='UNKNOWN' and inspector.reason=='LIGHTING_SHIFT'


def test_stale_and_duplicate_frames_never_confirm_rgb_presence_or_empty(tmp_path):
    cfg=configured(tmp_path);inspector=BasketInspector(cfg)
    assert inspector.update(empty(),1,1)=='UNKNOWN'
    assert inspector.update(empty(),1.11,1)=='UNKNOWN'
    assert inspector.update(empty(),1.22,1)=='UNKNOWN'
    assert inspector.update(empty(),2,1)=='UNKNOWN'


def test_roi_processing_and_overlay_do_not_mutate_policy_frame_or_camera_config(tmp_path):
    cfg=configured(tmp_path);image=empty();before=image.copy();hardware=copy.deepcopy(cfg['hardware'])
    assert crop(image,options(cfg)).shape==(240,320,3)
    verdict(cfg,image);preview=draw_region(image,options(cfg))
    np.testing.assert_array_equal(image,before)
    assert cfg['hardware']==hardware and not np.shares_memory(image,preview)


def test_reference_signature_rejects_polygon_change(tmp_path):
    cfg=configured(tmp_path);cfg['unload']['roi_polygon']=[[10,10],[630,10],[630,470],[10,470]]
    with pytest.raises(C.ConfigError,match='reference changed'):BasketInspector(cfg)


def test_old_small_reference_cannot_silently_run_new_detector(tmp_path):
    cfg=configured(tmp_path);path=C.path_value(cfg['unload']['reference'])
    np.savez_compressed(path,image=np.full((120,160,3),230,np.uint8),roi=cfg['unload']['roi'])
    with pytest.raises(C.ConfigError,match='register again'):BasketInspector(cfg)


def test_local_change_is_also_used_for_estimated_transfer(tmp_path):
    cfg=configured(tmp_path);inspector=BasketInspector(cfg);before=crop(empty(),options(cfg))
    image=empty();image[200:218,300:318]=220;after=crop(image,options(cfg))
    assert inspector.transfer_changed(before,after)
    assert inspector.transfer_changed(after,before)
    assert not inspector.transfer_changed(before,before)


@pytest.mark.parametrize('polygon',[[[0,0],[640,0],[0,20]],[[10,10],[40,40],[10,40],[40,10]],[[1,1],[2,1],[1,2]]])
def test_invalid_or_crossed_polygons_rejected(tmp_path,polygon):
    cfg=configured(tmp_path);cfg['unload']['roi_polygon']=polygon
    with pytest.raises(C.ConfigError):C.validate(cfg)


def test_roi_edit_invalidates_full_view_confirmation(tmp_path):
    from omx_manager.cli import edit_value
    cfg=configured(tmp_path)
    updated=edit_value(cfg,'unload.roi','[10, 10, 600, 440]')
    assert not updated['unload']['full_view_confirmed']


@pytest.mark.parametrize('sigma',[1.,2.])
def test_low_sensor_noise_does_not_turn_empty_basket_into_object(tmp_path,sigma):
    cfg=configured(tmp_path);inspector=BasketInspector(cfg);rng=np.random.default_rng(17)
    for i in range(8):
        image=np.clip(empty().astype(float)+rng.normal(0,sigma,(480,640,3)),0,255).astype(np.uint8)
        result=inspector.update(image,1+i*.11,1+i*.11)
    assert result=='EMPTY',inspector.snapshot(2)


@pytest.mark.parametrize('device',['omx3','omx4'])
def test_rgb_controller_two_consecutive_two_small_item_jobs(device,tmp_path,monkeypatch):
    from test_cycle_counting import trained
    import test_workflow_recovery as F
    cfg=trained(device,wrist=0)
    vision=configured(tmp_path,empty(100))['unload'];cfg['unload']=vision
    cfg['unload']['reference_binding']=registration_binding(cfg)
    def image(objects=0):
        result=empty(100)
        for i in range(objects):result[100:116,100+i*80:116+i*80]=110
        return result
    monkeypatch.setattr(F,'image',image)
    run,phases,sent,done,path=F.controller(cfg,tmp_path,monkeypatch,paired=True,jobs=2)
    run()
    assert len(done)==2 and phases.count('RUNNING')==4 and sent
    assert all(row['estimated_transfers']==2 and row['basket_empty_verified'] for row in done)


def test_roi_upgrade_preserves_learned_cycles_and_home(tmp_path):
    from test_cycle_counting import trained
    cfg=trained('omx3');before=copy.deepcopy(cfg)
    result=tune(cfg)
    assert result['unload']['vision_method']=='rgb_roi'
    for key in ('hardware','home','models','counting'):assert result[key]==before[key]


def test_invalid_reference_fails_before_camera_open(tmp_path,monkeypatch):
    from omx_manager import commissioning
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    cfg=configured(tmp_path);Path(cfg['unload']['reference']).unlink()
    camera_factory=Mock();monkeypatch.setattr(commissioning,'camera_for',camera_factory)
    monkeypatch.setattr(commissioning,'require_home',lambda _:None)
    device=NS(read=lambda:cfg['home']['target'].copy())
    with pytest.raises(OSError):commissioning.basket_operation(cfg,device,watch=True)
    camera_factory.assert_not_called()


@pytest.mark.parametrize('bad',['coverage','clipping'])
def test_automatic_unload_preflight_blocks_unverified_empty_view(tmp_path,monkeypatch,bad):
    from omx_manager import commissioning
    cfg=configured(tmp_path,empty(255) if bad=='clipping' else empty())
    if bad=='coverage':cfg['unload']['full_view_confirmed']=False
    monkeypatch.setattr(commissioning,'require_home',lambda _:None)
    import omx_manager.cycles as cycles
    monkeypatch.setattr(cycles,'require_profile',lambda *a:None)
    with pytest.raises(C.ConfigError,match='overexposed' if bad=='clipping' else 'entire basket'):
        commissioning.require_commissioned(cfg)


def test_overexposure_does_not_get_bypassed_by_strong_local_object_candidate(tmp_path):
    reference=empty(255);reference[:200]=100
    cfg=configured(tmp_path,reference);current=reference.copy();current[40:100,100:160]=140
    result,inspector=verdict(cfg,current)
    assert result=='UNKNOWN' and inspector.reason=='ROI_OVEREXPOSED'
    assert inspector.snapshot(1)['quality_flags']==['ROI_OVEREXPOSED']
    assert inspector.snapshot(1)['changed_ratio']>0
    assert inspector.snapshot(1)['features_computed'] is False


def test_unconfirmed_view_also_blocks_occupied_candidate(tmp_path):
    cfg=configured(tmp_path);cfg['unload']['full_view_confirmed']=False
    current=empty();current[100:130,100:130]=200
    result,inspector=verdict(cfg,current)
    assert result=='UNKNOWN' and inspector.reason=='FULL_VIEW_NOT_CONFIRMED'


def test_edge_changes_without_pixel_support_do_not_assert_object(tmp_path,monkeypatch):
    cfg=configured(tmp_path);inspector=BasketInspector(cfg);compare=inspector.comparator
    original=compare.features
    def moving_edges(image):
        gray,contrast,edges=original(image)
        edges[50:100,50:100]=255
        return gray,contrast,edges
    monkeypatch.setattr(compare,'features',moving_edges)
    for i in range(5):result=inspector.update(empty(),1+i*.11,1+i*.11)
    row=inspector.snapshot(2)
    assert result=='UNKNOWN' and row['reason']=='UNSUPPORTED_EDGE_CHANGE'
    assert row['changed_ratio']==0 and row['supported_edge_pixels']==0 and row['color_component_pixels']==0
    assert row['edge_changed_pixels']>=2000


@pytest.mark.parametrize('sigma',[1.,2.])
def test_textured_background_noise_never_asserts_object_without_strong_color_component(tmp_path,sigma):
    rng=np.random.default_rng(222)
    base=np.clip(100+rng.normal(0,4,(480,640,1)),0,255).astype(np.uint8)
    reference=np.repeat(base,3,axis=2);cfg=configured(tmp_path,reference)
    inspector=BasketInspector(cfg)
    for i in range(6):
        frame=np.clip(reference.astype(float)+rng.normal(0,sigma,reference.shape),0,255).astype(np.uint8)
        result=inspector.update(frame,1+i*.11,1+i*.11)
        assert result!='OCCUPIED',inspector.snapshot(2)


def test_invalid_quality_skips_expensive_features_and_keeps_model_frame_unchanged(tmp_path,monkeypatch):
    from unittest.mock import Mock
    cfg=configured(tmp_path,empty(255));inspector=BasketInspector(cfg);image=empty(255);before=image.copy()
    feature=Mock(side_effect=AssertionError('Invalid view must not run CLAHE/Canny'))
    monkeypatch.setattr(inspector.comparator,'features',feature)
    assert inspector.update(image,1,1)=='UNKNOWN'
    feature.assert_not_called();np.testing.assert_array_equal(image,before)


def test_comparison_view_shows_low_threshold_evidence_without_changing_inputs(tmp_path):
    from omx_manager.rgb_roi import comparison_view,mask_for
    cfg=configured(tmp_path);spec=options(cfg)
    reference=np.full((240,320,3),80,np.uint8);current=reference.copy();current[90:110,110:130]+=8
    ref_before=reference.copy();current_before=current.copy()
    panel,details=comparison_view(reference,current,spec)
    assert panel.shape==(304,960,3)
    assert details['global_changed_pixels']==0 and details['local_changed_pixels']==400
    assert details['component_boxes_roi'][0]==dict(x=110,y=90,width=20,height=20,pixels=400)
    assert (panel[32+100,640+120]==[0,0,255]).all()
    np.testing.assert_array_equal(reference,ref_before);np.testing.assert_array_equal(current,current_before)


def test_comparison_view_respects_polygon_mask(tmp_path):
    from omx_manager.rgb_roi import comparison_view
    cfg=configured(tmp_path,roi_polygon=[[160,80],[480,80],[480,400],[160,400]])
    reference=np.full((240,320,3),80,np.uint8);current=reference.copy();current[:,:30]=200
    _,details=comparison_view(reference,current,options(cfg))
    assert details['local_changed_pixels']==0 and details['component_boxes_roi']==[]
