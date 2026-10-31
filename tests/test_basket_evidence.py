import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import zipfile

import cv2
import numpy as np
import pytest
import yaml
from omx_manager import basket_evidence as B,exposure as E,config as C
from test_low_exposure import setup,reference,Camera


@pytest.fixture
def collection(setup,tmp_path,monkeypatch):
    cfg,clock,controls,top=setup
    cfg['hardware']['calibration_file']=str(tmp_path/'calibration.json')
    (tmp_path/'calibration.json').write_text('{}')
    reference(cfg,tmp_path)
    path=tmp_path/'config.yaml';path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(B,'ROOT',tmp_path)
    monkeypatch.setattr(B,'system_info',lambda:{'ip':{'stdout':'[{"addr_info":[{"local":"192.168.0.154"}]}]'}})
    wrist=Camera(clock,controls)
    device=NS(read=Mock(return_value={j:0. for j in C.JOINTS}),send=Mock(side_effect=AssertionError('No motor writes')))
    kwargs=dict(cameras={cfg['unload']['camera']:top,'wrist':wrist},clock=clock,sleep=clock.sleep,out=tmp_path/'evidence')
    return cfg,path,clock,controls,device,top,wrist,kwargs


def test_collects_all_labels_and_raw_rgb_without_changing_reference_or_config(collection):
    cfg,path,clock,controls,device,top,wrist,kwargs=collection
    original_config=path.read_bytes();ref=C.path_value(cfg['unload']['reference']);original_reference=ref.read_bytes()
    result=B.collect(cfg,path,device,input_fn=lambda _: '',**kwargs)
    assert result['status']=='COMPLETE' and result['scenes']==4
    with zipfile.ZipFile(result['archive']) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert [r['operator_ground_truth'] for r in manifest['scenes']]==['EMPTY','OCCUPIED','OCCUPIED','EMPTY']
        assert manifest['motor_writes'] is False and manifest['policy_executed'] is False
        assert not manifest['reference_changed'] and not manifest['config_changed'] and not manifest['exposure_restore_pending']
        assert manifest['reference_sha256_before']==hashlib.sha256(original_reference).hexdigest()
        assert len([n for n in z.namelist() if n.endswith('.png')])==48
        normal=cv2.imdecode(np.frombuffer(z.read('scenes/empty_before/normal/top/000.png'),np.uint8),cv2.IMREAD_COLOR)
        low=cv2.imdecode(np.frombuffer(z.read('scenes/empty_before/low/top/000.png'),np.uint8),cv2.IMREAD_COLOR)
        assert (normal==180).all() and (low==80).all()
        assert len(manifest['scenes'][0]['low'])==8
    assert controls.values==controls.initial and not E.journal_path(cfg).exists()
    assert path.read_bytes()==original_config and ref.read_bytes()==original_reference
    device.send.assert_not_called();top.disconnect.assert_called_once();wrist.disconnect.assert_called_once()


def test_cancel_still_packs_existing_state_and_releases_all_cameras(collection):
    cfg,path,clock,controls,device,top,wrist,kwargs=collection
    with pytest.raises(RuntimeError,match='ZIP'):
        B.collect(cfg,path,device,input_fn=lambda _:'q',**kwargs)
    archive=kwargs['out']/'omx4_evidence.zip'
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['status']=='INTERRUPTED' and manifest['scenes']==[]
        assert 'reference.npz' in z.namelist()
    top.disconnect.assert_called_once();wrist.disconnect.assert_called_once()
    assert controls.values==controls.initial


def test_failure_during_low_capture_restores_exposure_and_preserves_partial_evidence(collection,monkeypatch):
    cfg,path,clock,controls,device,top,wrist,kwargs=collection
    save=B.write_png
    def fail(path,frame):
        if 'low' in path.parts:raise RuntimeError('Injected disk/capture fault')
        save(path,frame)
    monkeypatch.setattr(B,'write_png',fail)
    with pytest.raises(RuntimeError,match='Injected'):
        B.collect(cfg,path,device,input_fn=lambda _:'',**kwargs)
    assert controls.values==controls.initial and not E.journal_path(cfg).exists()
    with zipfile.ZipFile(kwargs['out']/'omx4_evidence.zip') as z:
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['status']=='ERROR' and len(manifest['scenes'])==1
        assert 'scenes/empty_before/low/top/frames.json' in z.namelist()
    top.disconnect.assert_called_once();wrist.disconnect.assert_called_once()


def test_camera_connect_failure_closes_prior_camera_and_packages_error(collection):
    cfg,path,clock,controls,device,top,wrist,kwargs=collection
    wrist.connect.side_effect=RuntimeError('USB unavailable')
    with pytest.raises(RuntimeError,match='USB unavailable'):
        B.collect(cfg,path,device,input_fn=lambda _:'',**kwargs)
    top.disconnect.assert_called_once()
    with zipfile.ZipFile(kwargs['out']/'omx4_evidence.zip') as z:
        assert json.loads(z.read('manifest.json'))['status']=='ERROR'


def test_failed_sensor_restore_is_never_reported_complete(collection):
    cfg,path,clock,controls,device,top,wrist,kwargs=collection
    controls.fail_restore=True
    with pytest.raises(RuntimeError,match='restoration'):
        B.collect(cfg,path,device,input_fn=lambda _:'',**kwargs)
    assert E.journal_path(cfg).exists()
    with zipfile.ZipFile(kwargs['out']/'omx4_evidence.zip') as z:
        assert json.loads(z.read('manifest.json'))['exposure_restore_pending'] is True


def test_latest_archive_changes_without_losing_prior_capture(collection):
    cfg,path,clock,controls,device,top,wrist,kwargs=collection
    for _ in range(2):
        with pytest.raises(RuntimeError):B.collect(cfg,path,device,input_fn=lambda _:'q',**kwargs)
    assert len(list(kwargs['out'].glob('omx4_20*.zip')))==2
    assert (kwargs['out']/'omx4_evidence.zip').is_file()


def test_copy_never_exports_large_weights_and_redacts_sensitive_json(tmp_path,monkeypatch):
    cfg=C.load(Path(__file__).resolve().parents[1]/'params/omx4.yaml')
    model=tmp_path/'model';model.mkdir()
    (model/'model.safetensors').write_bytes(b'weights')
    (model/'policy_preprocessor_step_0_normalizer_processor.safetensors').write_bytes(b'stats')
    (model/'config.json').write_text('{"token":"secret","chunk_size":100}')
    monkeypatch.setattr(C,'profile',lambda _:dict(items={'F':{'path':str(model)}}))
    monkeypatch.setattr(B,'ROOT',tmp_path)
    folder=tmp_path/'bundle';manifest={};B.snapshot_existing(cfg,folder,manifest)
    assert not (folder/'model_metadata/F/model.safetensors').exists()
    assert (folder/'model_metadata/F/policy_preprocessor_step_0_normalizer_processor.safetensors').read_bytes()==b'stats'
    assert json.loads((folder/'model_metadata/F/config.json').read_text())['token']=='[REDACTED]'


def test_pack_does_not_follow_symlinks_outside_evidence(tmp_path):
    folder=tmp_path/'bundle';folder.mkdir();(tmp_path/'private').write_text('secret')
    (folder/'outside').symlink_to(tmp_path/'private');(folder/'manifest.json').write_text('{}')
    with zipfile.ZipFile(B.pack(folder,'omx4')) as z:assert z.namelist()==['manifest.json']
