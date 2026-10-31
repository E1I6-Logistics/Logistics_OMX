"""Empty reference capture retries bounded geometry failures; no hardware."""
import json
from types import SimpleNamespace as NS
import cv2,numpy as np,pytest
from omx_manager import commissioning as M,basket,config as C,exposure as E
from omx_manager import basket_registration as R
from omx_manager.simulation import Clock
from test_basket_registration import spec,registered_config,scene
from test_low_exposure import setup,reference


def test_full_view_capture_without_prior_geometry_has_bounded_threshold_fallback():
 image=np.full((480,640,3),10,np.uint8);image[110:251,230:391]=140
 cv2.fillConvexPoly(image,np.array([[205,180],[230,155],[230,205]],np.int32),(110,110,110))
 s=spec();single=R.Tracker(s);single._threshold_retry=True
 with pytest.raises(R.ViewError,match='POSE_NOT_FOUND'):single.extract(image)
 tracker=R.Tracker(s);before=image.copy();tracker.extract(image)
 assert tracker.last_info['registration_threshold_used'] in (1.45,1.55,1.25)
 assert tracker.last_profile['partial'] is False
 np.testing.assert_array_equal(image,before)


def test_single_pose_failure_does_not_abort_or_replace_existing_reference(tmp_path,monkeypatch):
 cfg=registered_config(tmp_path);clock=Clock();frame,_=scene();count=0
 original=R.Tracker.extract
 def extract(self,image):
  nonlocal count;count+=1
  if count==1:raise R.ViewError('BASKET_POSE_NOT_FOUND')
  return original(self,image)
 monkeypatch.setattr(R.Tracker,'extract',extract)
 monkeypatch.setattr(M,'time',NS(perf_counter=clock,sleep=clock.sleep))
 monkeypatch.setattr(basket,'time',NS(perf_counter=clock))
 monkeypatch.setattr('omx_manager.hooks.heartbeat',lambda:None)
 camera=NS(read_latest_with_timestamp=lambda **_:(frame,clock()))
 p=C.path_value(cfg['unload']['reference']);before=p.read_bytes()
 result=M._basket_connected(cfg,camera,lambda:None,None,True,False,0,0,0,{})
 assert result['image'].shape==(128,128,3) and count>=5
 assert p.read_bytes()==before


def test_capture_timeout_preserves_reference_and_restores_before_saving_diagnostic(setup,tmp_path,monkeypatch):
 cfg,clock,controls,camera=setup;camera.shape=(480,640,3);reference(cfg,tmp_path)
 cfg['unload']['registration_enabled']=True
 p=C.path_value(cfg['unload']['reference']);before=p.read_bytes()
 monkeypatch.setattr(M,'require_home',lambda _:None)
 monkeypatch.setattr(M,'camera_for',lambda _:camera)
 monkeypatch.setattr(M,'time',NS(perf_counter=clock,sleep=clock.sleep,time=lambda:1000+clock()))
 monkeypatch.setattr(C,'ROOT',tmp_path)
 monkeypatch.setattr('omx_manager.viewer.attach',lambda *a:None)
 device=NS(read=lambda:cfg['home']['target'])
 with pytest.raises(C.ConfigError,match='previous reference unchanged'):
  M.basket_operation(cfg,device,capture=True)
 assert controls.values==controls.initial and not E.journal_path(cfg).exists()
 assert p.read_bytes()==before
 row=json.loads((tmp_path/'state/manager/omx4/basket_capture_failure.json').read_text())
 assert row['restore_verified'] is True and row['existing_reference_unchanged'] is True
 assert row['reason']=='BASKET_CONTRAST_TOO_LOW'
 assert (tmp_path/'state/manager/omx4/basket_failure.png').is_file()
