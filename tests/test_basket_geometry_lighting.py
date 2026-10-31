"""Known field failures: clipped near-top corners and box-induced lighting bias."""
import cv2,numpy as np,pytest
from omx_manager import basket
from omx_manager import basket_registration as R
from test_basket_registration import spec,partial_spec,registered_config,scene


@pytest.mark.parametrize('corner_y',[6,10,14])
def test_real_near_top_corner_is_not_misidentified_as_a_sensor_cut(corner_y):
 s=partial_spec()
 points=np.array([[240,0],[365,0],[400,corner_y],[350,170],[170,110]],np.float32)
 q=R.top_partial_quad(cv2.convexHull(points),np.array([0,0]),s)
 assert q.shape==(4,2) and q[:,1].min()<0
 assert np.min(np.linalg.norm(q-[400,corner_y],axis=1))<1
 R.describe(q)


def test_partial_barely_touching_top_can_use_four_observed_corners():
 s=partial_spec();points=np.array([[250,2],[400,40],[350,170],[170,110]],np.float32)
 q=R.top_partial_quad(cv2.convexHull(points),np.array([0,0]),s)
 np.testing.assert_allclose(q,R.ordered(points))


def test_two_missing_top_corners_are_not_guessed_from_a_trapezoid():
 s=partial_spec();points=np.array([[200,0],[400,0],[350,170],[170,110]],np.float32)
 with pytest.raises(R.ViewError):R.top_partial_quad(cv2.convexHull(points),np.array([0,0]),s)

def test_simplification_cannot_bypass_extrapolation_limit():
 s=partial_spec();s['registration_max_extrapolation']=.05
 points=np.array([[240,0],[365,0],[400,10],[350,170],[170,110]],np.float32)
 with pytest.raises(R.ViewError,match='EXTRAPOLATION_LIMIT'):
  R.top_partial_quad(cv2.convexHull(points),np.array([0,0]),s)


def test_unusable_parallel_edges_are_not_extrapolated():
 s=partial_spec();points=np.array([[170,0],[390,0],[400,100],[350,170],[180,100]],np.float32)
 with pytest.raises(R.ViewError):R.top_partial_quad(cv2.convexHull(points),np.array([0,0]),s)


def boxed_shadow():
 ref=np.full((128,128,3),120,np.uint8);image=ref.copy()
 image[8:120,8:120]-=np.array([4,5,7],np.uint8)
 image[45:78,45:78]-=25
 return ref,image


def test_box_shadow_uses_independent_rim_without_increasing_lighting_limit():
 s=spec();ref,image=boxed_shadow();r=R.Comparator(ref,s).compare(image)
 assert s['registration_color_limit']==6
 assert r['interior_color_offset']==[-4.,-5.,-7.]
 assert r['lighting_offset_source']=='rim_consensus' and r['lighting_anchor_sides']==4
 assert r['color_offset']==[0.,0.,0.] and r['verdict']=='OCCUPIED'


@pytest.mark.parametrize('delta',[13,20,30])
def test_large_global_lighting_shift_remains_unknown(delta):
 s=spec();ref=np.full((128,128,3),120,np.uint8);image=ref-delta
 r=R.Comparator(ref,s).compare(image)
 assert r['verdict']=='UNKNOWN' and r['quality_flags']==['LIGHTING_SHIFT']

@pytest.mark.parametrize('delta',[7,10,12])
def test_moderate_shift_is_compensated_only_with_wall_consensus(delta):
 s=spec();ref=np.full((128,128,3),120,np.uint8);image=ref-delta
 r=R.Comparator(ref,s).compare(image)
 assert r['verdict']=='EMPTY' and r['lighting_offset_source']=='rim_consensus'
 assert r['lighting_anchor_sides']==4 and r['color_offset']==[-delta]*3

def test_moderate_shift_plus_small_box_is_still_occupied():
 s=spec();ref=np.full((128,128,3),120,np.uint8);image=ref-7;image[55:69,55:69]-=25
 assert R.Comparator(ref,s).compare(image)['verdict']=='OCCUPIED'


def test_shadow_without_a_clear_object_does_not_authorize_empty():
 s=spec();ref,image=boxed_shadow();image[45:78,45:78]+=25
 r=R.Comparator(ref,s).compare(image)
 assert r['verdict']=='UNKNOWN' and r['evidence_reason']=='REGISTERED_FAINT_CHANGE'


def test_rim_correction_requires_three_agreeing_observed_sides():
 s=spec();ref,image=boxed_shadow()
 valid=np.ones((128,128),bool);valid[:6]=False;valid[-6:]=False
 r=R.Comparator(ref,s).compare(image,valid)
 assert r['verdict']=='UNKNOWN' and r['lighting_offset_source']=='interior'


def test_warmup_must_not_hide_lighting_rejection(tmp_path):
 cfg=registered_config(tmp_path);inspector=basket.BasketInspector(cfg)
 frame,_=scene();frame=(frame.astype(np.int16)*.5).astype(np.uint8)
 inspector.update(frame,1.,1.)
 r=inspector.snapshot(1.)
 assert r['verdict']=='UNKNOWN'
 # Depending on contour threshold, geometry can be rejected before lighting.
 assert r['reason']!='TEMPORAL_WARMUP' if r.get('quality_flags') else True


def test_low_level_quality_flags_survive_temporal_buffer_warmup(tmp_path,monkeypatch):
 cfg=registered_config(tmp_path);inspector=basket.BasketInspector(cfg);frame,_=scene()
 monkeypatch.setattr(inspector.comparator,'compare',lambda *a:dict(verdict='UNKNOWN',changed_ratio=0,
     evidence_reason='LIGHTING_SHIFT',quality_flags=['LIGHTING_SHIFT'],features_computed=False))
 inspector.update(frame,1,1)
 assert inspector.snapshot(1)['reason']=='LIGHTING_SHIFT'
 assert inspector.snapshot(1)['temporal_samples']==1


def test_partial_view_keeps_original_illumination_estimator():
 s=partial_spec();ref,image=boxed_shadow();r=R.Comparator(ref,s).compare(image)
 assert r['lighting_offset_source']=='interior' and r['view_scope']=='visible_only'
