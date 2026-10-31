"""Only positive object evidence can bypass a partial-view lighting rejection."""
import hashlib,json
import numpy as np,pytest
from omx_manager import basket_registration as R
from omx_manager.rgb_roi import signature
from test_basket_registration import partial_spec,spec


def shadow_scene(item=True):
 ref=np.full((128,128,3),120,np.uint8)
 image=np.empty_like(ref);image[:]=[107,103,93]
 if item:image[45:75,45:75]=[100,70,40]
 return ref,image


def test_visible_colored_box_remains_occupied_with_27_unit_background_difference():
 s=partial_spec();ref,image=shadow_scene()
 r=R.Comparator(ref,s).compare(image)
 assert r['verdict']=='OCCUPIED' and r['evidence_reason']=='REGISTERED_COLORED_OBJECT'
 assert r['quality_warnings']==['LIGHTING_SHIFT'] and r['quality_flags']==[]
 assert r['chroma_component_pixels']>=s['registration_chroma_component_pixels']
 assert r['view_scope']=='visible_only' and not r['full_basket_verified']


def test_stacked_colored_items_need_no_count_or_instance_separation():
 s=partial_spec();ref,image=shadow_scene(False)
 image[40:65,40:65]=[100,70,40];image[52:78,52:78]=[130,105,65]
 r=R.Comparator(ref,s).compare(image)
 assert r['verdict']=='OCCUPIED' and r['evidence_reason']=='REGISTERED_COLORED_OBJECT'


def test_pure_global_color_or_brightness_shift_never_becomes_occupied_or_empty():
 s=partial_spec();ref,image=shadow_scene(False)
 for current in (image,ref-27):
  r=R.Comparator(ref,s).compare(current)
  assert r['verdict']=='UNKNOWN' and r['quality_flags']==['LIGHTING_SHIFT']
  assert r['chroma_component_pixels']==0


def test_faint_or_tiny_color_noise_cannot_authorize_actions():
 s=partial_spec();ref,image=shadow_scene(False);image[50:54,50:54]=[120,40,20]
 assert R.Comparator(ref,s).compare(image)['verdict']=='UNKNOWN'


def test_gray_shadow_alone_cannot_use_the_color_fallback():
 s=partial_spec();ref=np.full((128,128,3),120,np.uint8);image=ref-27;image[40:70,40:70]=50
 r=R.Comparator(ref,s).compare(image)
 assert r['verdict']=='UNKNOWN' and r['chroma_component_pixels']==0

def test_additive_color_shift_on_a_shaded_empty_floor_cannot_become_an_object():
 s=partial_spec();gradient=np.linspace(65,170,128).astype(np.uint8)
 ref=np.repeat(np.tile(gradient,(128,1))[:,:,None],3,axis=2)
 image=ref-np.array([13,17,27],np.uint8)
 r=R.Comparator(ref,s).compare(image)
 assert r['verdict']=='UNKNOWN' and r['chroma_component_pixels']==0


@pytest.mark.parametrize('value',[0,255])
def test_exposure_failures_still_block_color_evidence(value):
 s=partial_spec();ref,image=shadow_scene();image[:]=value;image[45:75,45:75]=[100,70,40]
 r=R.Comparator(ref,s).compare(image)
 assert r['verdict']=='UNKNOWN' and 'chroma_component_pixels' not in r


def test_unseen_pixels_do_not_supply_color_evidence():
 s=partial_spec();ref,image=shadow_scene();valid=np.ones((128,128),bool);valid[40:80,40:80]=False
 r=R.Comparator(ref,s).compare(image,valid)
 assert r['verdict']=='UNKNOWN' and r['chroma_component_pixels']==0


def test_fallback_can_be_disabled_and_does_not_change_full_view_mode():
 ref,image=shadow_scene();s=partial_spec();s['registration_partial_chroma_fallback']=False
 assert R.Comparator(ref,s).compare(image)['verdict']=='UNKNOWN'
 r=R.Comparator(ref,spec()).compare(image)
 assert r['verdict']=='UNKNOWN' and 'chroma_component_pixels' not in r


def test_normal_empty_and_occupied_path_does_not_compute_color(monkeypatch):
 s=partial_spec();ref=np.full((128,128,3),120,np.uint8);c=R.Comparator(ref,s)
 monkeypatch.setattr(c,'chromatic_change',lambda *a:(_ for _ in ()).throw(AssertionError('unnecessary color work')))
 assert c.compare(ref)['verdict']=='EMPTY'
 current=ref.copy();current[50:70,50:70]-=25
 assert c.compare(current)['verdict']=='OCCUPIED'


def test_default_fallback_preserves_installed_reference_signature():
 s=partial_spec()
 new={'registration_partial_chroma_fallback','registration_chroma_threshold','registration_chroma_component_pixels'}
 before={k:s[k] for k in R.DEFAULTS if k not in new and k!='registration_wall_color_limit'}
 old=dict(version=3,size=(320,240),roi=s['roi'],polygon=s['roi_polygon'],registration=before)
 assert signature(s)==hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()


def test_comparison_view_uses_the_same_color_evidence():
 s=partial_spec();ref,image=shadow_scene();view,r=R.comparison_view(ref,image,s)
 assert view.shape==(288,768,3) and r['difference_basis']=='normalized_color'
