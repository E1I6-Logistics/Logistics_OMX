"""Synthetic moving tray, edge objects, geometry rejection and real manager integration."""
import copy,json
from pathlib import Path
import cv2,numpy as np,pytest
from omx_manager import config as C,basket,commissioning
from omx_manager import basket_registration as R
from omx_manager.rgb_roi import signature
from omx_manager.workflow import registration_binding
ROOT=Path(__file__).resolve().parents[1]


def spec():
 c=commissioning.tune(C.load(ROOT/'params/omx4.yaml'))
 c['unload'].update(roi=[190,70,240,220],registration_enabled=True,low_exposure_enabled=True,inspection_exposure=83.,vision_method="low_template",full_view_confirmed=True)
 return basket.options(c)


def scene(shift=(0,0),angle=0.,item=None):
 # Inverse-warp a known tray; avoid replacing/rotating any model camera input.
 tray=np.full((128,128,3),140,np.uint8)
 if item is not None:
  x,y,size,contrast=item;tray[y:y+size,x:x+size]=140-contrast
 q=np.array([[250,90],[410,130],[355,270],[190,215]],np.float32)
 center=q.mean(0);rad=np.deg2rad(angle);rot=np.array([[np.cos(rad),-np.sin(rad)],[np.sin(rad),np.cos(rad)]])
 q=((q-center)@rot.T+center+shift).astype(np.float32)
 original=np.array([[0,0],[127,0],[127,127],[0,127]],np.float32)
 homography=cv2.getPerspectiveTransform(original,q)
 image=cv2.warpPerspective(tray,homography,(640,480),borderValue=(10,10,10))
 return image,q


@pytest.mark.parametrize('shift,angle',[((0,0),0),((25,-8),4),((-18,15),-7)])
def test_moving_empty_tray_is_registered_without_background_changes(shift,angle):
 s=spec();image,_=scene();tracker=R.Tracker(s);ref=tracker.extract(image);profile=tracker.last_profile
 current,_=scene(shift,angle);before=current.copy()
 aligned=R.Tracker(s,profile).extract(current)
 assert R.Comparator(ref,s).compare(aligned)['verdict']=='EMPTY'
 np.testing.assert_array_equal(current,before)


@pytest.mark.parametrize('place',[(57,57),(8,18),(105,18),(105,105),(8,105)])
def test_two_cm_equivalent_objects_remain_detectable_near_walls(place):
 s=spec();image,_=scene();tracker=R.Tracker(s);ref=tracker.extract(image)
 # About 2/18 of 128 = 14 pixels, rendered before projective camera sampling.
 current,_=scene((20,-8),4,item=(*place,14,25))
 aligned=R.Tracker(s,tracker.last_profile).extract(current)
 assert R.Comparator(ref,s).compare(aligned)['verdict']=='OCCUPIED'


def test_faint_large_change_is_unknown_not_empty():
 s=spec();ref=np.full((128,128,3),120,np.uint8);current=ref.copy();current[40:70,40:70]-=7
 assert R.Comparator(ref,s).compare(current)['verdict']=='UNKNOWN'


@pytest.mark.parametrize('change',[dict(registration_max_shift_px=10),dict(registration_max_rotation_deg=1)])
def test_motion_outside_approved_envelope_is_not_accepted(change):
 s=spec();ref,_=scene();tracker=R.Tracker(s);tracker.extract(ref);s.update(change)
 current,_=scene((35,0),8)
 with pytest.raises(R.ViewError):R.Tracker(s,tracker.last_profile).extract(current)


def test_clipped_tray_cannot_be_registered_even_if_white_area_is_large():
 s=spec();image,_=scene((0,-160))
 with pytest.raises(R.ViewError,match='CLIPPED'):R.Tracker(s).extract(image)


def test_blank_frame_is_unknown_not_an_empty_basket():
 with pytest.raises(R.ViewError):R.Tracker(spec()).extract(np.zeros((480,640,3),np.uint8))


def test_registration_profile_rejects_camera_size_and_corrupt_geometry():
 s=spec();image,_=scene();tracker=R.Tracker(s);tracker.extract(image);p=tracker.last_profile
 with pytest.raises(C.ConfigError,match='shape'):R.validate_profile(p,s,(720,1280))
 p['quad'][0]=[float('nan'),3]
 with pytest.raises(C.ConfigError):R.validate_profile(p,s,(480,640))


def test_modes_and_geometry_limits_are_validated():
 s=spec();s['low_exposure_enabled']=False
 with pytest.raises(C.ConfigError):R.validate(s)
 s=spec();s['roi_polygon']=[[200,100],[300,100],[300,200],[200,200]]
 with pytest.raises(C.ConfigError,match='polygon'):R.validate(s)
 s=spec();s['registration_inner_margin']=12
 with pytest.raises(C.ConfigError):R.validate(s)


def registered_config(tmp_path):
 cfg=commissioning.tune(C.load(ROOT/'params/omx4.yaml'));cfg['unload'].update(spec())
 cfg['home']['target']={j:0. for j in C.JOINTS}
 frame,_=scene();tracker=R.Tracker(basket.options(cfg));ref=tracker.extract(frame)
 path=tmp_path/'registered.npz';exposure=dict(backend='opencv-v4l2',controls=dict(auto_exposure=1.,exposure=83.,gain=20.))
 np.savez_compressed(path,image=ref,roi=cfg['unload']['roi'],roi_signature=signature(basket.options(cfg)),
                     registration_profile=json.dumps(tracker.last_profile),exposure_profile=json.dumps(exposure))
 cfg['unload'].update(reference=str(path),reference_binding=registration_binding(cfg))
 return cfg


def test_actual_inspector_uses_fresh_registered_frames_and_clears_on_pose_loss(tmp_path):
 cfg=registered_config(tmp_path);inspector=basket.BasketInspector(cfg)
 frame,_=scene((20,-7),3)
 for i in range(5):result=inspector.update(frame,1+i*.11,1+i*.11)
 assert result=='EMPTY' and inspector.snapshot(2)['registration']=='POSE_FOUND'
 occupied,_=scene((20,-7),3,item=(90,90,14,25))
 for i in range(5):result=inspector.update(occupied,2+i*.11,2+i*.11)
 assert result=='OCCUPIED'
 assert inspector.update(np.zeros_like(frame),3,3)=='UNKNOWN'
 assert inspector.latest is None and inspector.samples==0
 assert inspector.snapshot(3)['registration']=='REJECTED'
 assert inspector.update(occupied,4,3)=='UNKNOWN' # stale frame cannot authorize actions


def test_registered_reference_is_not_reused_in_legacy_mode(tmp_path):
 cfg=registered_config(tmp_path);cfg['unload']['registration_enabled']=False
 with pytest.raises(C.ConfigError,match='Registration reference'):basket.BasketInspector(cfg)


def test_old_reference_cannot_silently_enable_registration(tmp_path):
 cfg=registered_config(tmp_path);p=Path(cfg['unload']['reference'])
 with np.load(p) as d:fields={k:d[k] for k in d.files if k!='registration_profile'}
 np.savez_compressed(p,**fields)
 with pytest.raises(C.ConfigError,match='new moving-tray'):basket.BasketInspector(cfg)


def test_capture_and_diagnostics_share_the_canonical_shape_and_do_not_move_robot(tmp_path):
 cfg=registered_config(tmp_path);s=basket.options(cfg);frame,_=scene();canonical=basket.crop(frame,s)
 inspector=basket.BasketInspector(cfg)
 assert canonical.shape==(128,128,3)
 view,_=R.comparison_view(inspector.reference,canonical,s)
 assert view.shape==(288,768,3)
 tracker=R.Tracker(s);tracker.extract(frame);before=frame.copy();pose=R.pose_view(frame,s,tracker.last_profile)
 np.testing.assert_array_equal(frame,before);assert pose.shape==frame.shape


def test_registration_toggle_preserves_home_models_and_requires_view_confirmation():
 from omx_manager.cli import edit_value
 cfg=commissioning.tune(C.load(ROOT/'params/omx4.yaml'));cfg['unload']['full_view_confirmed']=True
 cfg['unload'].update(low_exposure_enabled=True,inspection_exposure=83.,vision_method='low_template')
 original=copy.deepcopy(cfg)
 result=edit_value(cfg,'unload.registration_enabled','true')
 assert result['unload']['registration_enabled'] and not result['unload']['full_view_confirmed']
 for key in ('hardware','home','models','counting'):assert result[key]==original[key]


def test_normalization_does_not_make_a_moving_basket_look_stationary(tmp_path):
 cfg=registered_config(tmp_path);inspector=basket.BasketInspector(cfg)
 for i in range(6):
  image,_=scene((i*5,0))
  assert inspector.update(image,1+i*.11,1+i*.11)=='UNKNOWN'
 final,_=scene((25,0))
 for i in range(5):result=inspector.update(final,2+i*.11,2+i*.11)
 assert result=='EMPTY' and inspector.snapshot(3)['pose_stable']


def test_slow_accumulating_motion_resets_pose_confirmation_anchor(tmp_path):
 cfg=registered_config(tmp_path);cfg['unload']['confirmation_s']=1.
 inspector=basket.BasketInspector(cfg)
 for i in range(12):
  image,_=scene((i*2,0))
  assert inspector.update(image,1+i*.11,1+i*.11)=='UNKNOWN'


def partial_scene(item=None):
 return scene((0,-110),item=item)[0]


def partial_spec():
 s=spec();s.update(roi=[190,0,240,230],registration_allow_partial=True,registration_visible_confirmed=True,full_view_confirmed=False)
 return s


def test_partial_top_cut_uses_only_observed_pixels_and_explicit_scope():
 s=partial_spec();tracker=R.Tracker(s);reference=tracker.extract(partial_scene())
 assert tracker.last_profile['partial'] and not tracker.valid_mask.all()
 assert tracker.last_info['view_scope']=='visible_only'
 assert tracker.last_info['full_basket_verified'] is False
 current=R.Tracker(s,tracker.last_profile);image=current.extract(partial_scene((57,57,14,30)))
 result=R.Comparator(reference,s,tracker.valid_mask).compare(image,current.valid_mask)
 assert result['verdict']=='OCCUPIED' and result['view_scope']=='visible_only'
 assert not result['full_basket_verified']
 empty=R.Comparator(reference,s,tracker.valid_mask).compare(reference,tracker.valid_mask)
 assert empty['verdict']=='EMPTY' and empty['view_scope']=='visible_only' and not empty['full_basket_verified']


def test_partial_requires_visible_area_confirmation_not_full_view_checkbox():
 s=partial_spec();s.update(registration_visible_confirmed=False,full_view_confirmed=True)
 t=R.Tracker(s);image=t.extract(partial_scene())
 assert R.Comparator(image,s,t.valid_mask).compare(image,t.valid_mask)['evidence_reason']=='VISIBLE_VIEW_NOT_CONFIRMED'


def test_partial_rejects_insufficient_common_view_and_excessive_extrapolation():
 s=partial_spec();t=R.Tracker(s);reference=t.extract(partial_scene())
 comp=R.Comparator(reference,s,t.valid_mask)
 result=comp.compare(reference,np.zeros((128,128),bool))
 assert result['verdict']=='UNKNOWN' and result['evidence_reason']=='INSUFFICIENT_COMMON_VIEW'
 with pytest.raises(R.ViewError):R.Tracker(s).extract(scene((0,-230))[0])


def test_partial_reference_is_not_accepted_as_full_view():
 s=partial_spec();t=R.Tracker(s);t.extract(partial_scene());s['registration_allow_partial']=False
 with pytest.raises(C.ConfigError,match='Partial reference'):R.validate_profile(t.last_profile,s,(480,640))


def test_partial_visible_mask_survives_npz_and_inspector_reset(tmp_path):
 cfg=commissioning.tune(C.load(ROOT/'params/omx3.yaml'));cfg['unload'].update(partial_spec())
 cfg['home']['target']={j:0. for j in C.JOINTS}
 t=R.Tracker(basket.options(cfg));image=t.extract(partial_scene())
 path=tmp_path/'partial.npz'
 exposure=dict(backend='opencv-v4l2',controls=dict(auto_exposure=1.,exposure=83.,gain=20.))
 np.savez_compressed(path,image=image,roi=cfg['unload']['roi'],roi_signature=signature(basket.options(cfg)),
                     registration_profile=json.dumps(t.last_profile),registration_mask=t.valid_mask,exposure_profile=json.dumps(exposure))
 cfg['unload'].update(reference=str(path),reference_binding=registration_binding(cfg))
 inspector=basket.BasketInspector(cfg)
 for i in range(5):result=inspector.update(partial_scene(),1+i*.11,1+i*.11)
 assert result=='EMPTY' and inspector.snapshot(2)['view_scope']=='visible_only'
 assert inspector.snapshot(2)['full_basket_verified'] is False


def test_unseen_pixels_never_become_whole_basket_evidence():
 s=partial_spec();t=R.Tracker(s);reference=t.extract(partial_scene());changed=reference.copy()
 changed[~t.valid_mask]=[255,0,0]
 result=R.Comparator(reference,s,t.valid_mask).compare(changed,t.valid_mask)
 assert result['verdict']=='EMPTY' and result['view_scope']=='visible_only'
 assert result['full_basket_verified'] is False
