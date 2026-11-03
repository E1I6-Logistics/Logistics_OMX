"""No camera streams, motor writes or policy inference in configuration migration."""
import copy
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from omx_manager import config as C, model_compat as M, runtime as R

ROOT = Path(__file__).resolve().parents[1]


def original():
    cfg = C.load(ROOT / 'params/omx3.yaml')
    cfg['models']['active_profile'] = 'original'
    cfg['hardware']['cameras']['top'] = dict(type='opencv', index_or_path='/old-c920', width=640, height=480, fps=30)
    cfg['hardware']['cameras']['wrist']['index_or_path'] = '/omx3-specific-wrist'
    cfg['home']['target'] = {j: 1. for j in C.JOINTS}
    cfg['home']['binding'] = 'old-model-camera-binding'
    cfg['counting']['profiles'] = {'E': {'old': 'e-profile'}}
    cfg['workflow']['items'] = {'E': {'old': 'stages'}}
    cfg['recovery']['routes'] = {'old': {'path': 'model-specific'}}
    cfg['unload'].update(inspection_enabled=True, low_exposure_enabled=True, inspection_exposure=81,
                         reference='/old-c920.npz', reference_binding='old-ref', vision_method='low_template', registration_enabled=True)
    return cfg


def test_temp_model_contract_preserves_omx3_identity_and_original_model():
    cfg = original(); before = copy.deepcopy(cfg)
    result = M.prepare(cfg, '920312070999')
    assert cfg == before
    source = C.load(ROOT / 'params/omx4.yaml')
    assert C.profile(result)['items']['E'] == C.profile(source)['items']['F']
    assert result['device_id'] == 'omx3' and result['role'] == 'unload'
    assert result['models']['profiles']['original'] == cfg['models']['profiles']['original']
    for key in ('port', 'robot_id', 'calibration_file', 'disable_torque_on_disconnect'):
        assert result['hardware'][key] == cfg['hardware'][key]
    assert result['home']['target'] == cfg['home']['target']
    assert result['runtime'] == cfg['runtime']
    assert result['hardware']['cameras']['wrist']['index_or_path'] == '/omx3-specific-wrist'
    top = result['hardware']['cameras']['top']
    assert top['serial_number_or_name'] == '920312070999' and top['type'] == 'intelrealsense'
    assert not top['use_depth'] and top['use_rgb'] and 'index_or_path' not in top
    assert result['home']['binding'] is None and result['counting']['profiles'] == {}
    assert result['workflow']['items'] == {} and result['recovery']['routes'] == {}
    assert result['unload']['reference'] is None
    assert not result['unload']['inspection_enabled'] and not result['unload']['low_exposure_enabled']
    assert not result['unload']['registration_enabled']
    argv, mqtt, env, _ = R.build(result, ROOT / 'params/omx3.yaml')
    assert '--robot-id' in mqtt and 'omx3' in mqtt
    assert '--task=' + C.profile(source)['items']['F']['task'] in argv
    assert env['OMX_UNLOAD_MODE'] == 'cycles'
    assert json.loads(next(x.split('=', 1)[1] for x in argv if x.startswith('--robot.cameras='))) == result['hardware']['cameras']


def test_repeat_application_keeps_newly_registered_home_and_cycle_profile():
    cfg = M.prepare(original(), '920312070999')
    cfg['home']['binding'] = 'new-binding'
    cfg['counting']['profiles']['E'] = dict(binding='new-binding', center=dict(cfg['home']['target']),
        tolerance={j:4. for j in C.JOINTS[:-1]}, leave_tolerance=12., confirmed_cycles=3,
        min_excursion=25., min_cycle_s=5., source='operator_confirmed_model_rollout')
    before = copy.deepcopy(cfg)
    assert M.prepare(cfg, '920312070999') == before
    assert M.prepare(cfg, '920312070998')['home']['binding'] is None


@pytest.mark.parametrize('device', ['omx1', 'omx2', 'omx4'])
def test_other_robot_rejected_without_mutation(device):
    cfg = C.load(ROOT / f'params/{device}.yaml'); before = copy.deepcopy(cfg)
    with pytest.raises(C.ConfigError, match='OMX3'): M.prepare(cfg, '920312070999')
    assert cfg == before


@pytest.mark.parametrize('value', ['', 'SET_OMX3_REALSENSE_SERIAL', '0; echo test', '１２３'])
def test_serial_must_be_real_ascii_digits(value):
    with pytest.raises(C.ConfigError): M.resolve_serial(value)


@pytest.mark.parametrize('serials', [[], ['1', '2'], ['123456789012']])
def test_auto_serial_enumerates_only_and_refuses_ambiguity(monkeypatch, serials):
    rs = NS(camera_info=NS(serial_number='serial'), context=lambda: NS(query_devices=lambda: [NS(get_info=lambda _, s=s: s) for s in serials]))
    monkeypatch.setitem(sys.modules, 'pyrealsense2', rs)
    if len(serials) == 1: assert M.resolve_serial('auto') == serials[0]
    else:
        with pytest.raises(C.ConfigError): M.resolve_serial('auto')


def local_files(cfg, tmp_path):
    cal = tmp_path / (cfg['hardware']['robot_id'] + '.json')
    C.write_json(cal, {j: dict(id=C.IDS[j], drive_mode=0, range_min=0, range_max=4095, homing_offset=0) for j in C.JOINTS})
    cfg['hardware']['calibration_file'] = str(cal)
    folder = tmp_path / 'f-model'; folder.mkdir()
    C.write_json(folder / 'config.json', dict(type='act', input_features={f'observation.images.{k}': dict(shape=[3,480,640]) for k in ('top', 'wrist')}, output_features=dict(action=dict(shape=[6]))))
    (folder / 'model.safetensors').write_bytes(b'fake, never loaded')
    for name in ('policy_preprocessor.json', 'policy_postprocessor.json'): C.write_json(folder / name, dict(steps=[]))
    return folder


def test_apply_checks_artifacts_then_backs_up_yaml_without_touching_jobs(tmp_path, monkeypatch):
    cfg = original(); cfg['counting']['profiles'] = {}; cfg['workflow']['items'] = {}; cfg['recovery']['routes'] = {}
    folder = local_files(cfg, tmp_path)
    path = tmp_path / 'omx3.yaml'; C.save(path, cfg); old = path.read_bytes()
    monkeypatch.setattr(R, 'device_lock', lambda _: nullcontext())
    from omx_manager import exposure
    monkeypatch.setattr(exposure, 'require_restored', lambda _: None)
    preview = M.configure(path, '123456789012', str(folder))
    assert not preview['applied'] and path.read_bytes() == old
    result = M.configure(path, '123456789012', str(folder), apply=True)
    assert result['applied'] and not result['motor_writes']
    assert any(p.read_bytes() == old for p in (tmp_path / 'backups').iterdir())
    updated = C.load(path); C.check_files(updated)
    with pytest.raises(C.ConfigError, match='binding changed'): C.require_home(updated)
    assert not (tmp_path / 'jobs.json').exists()
    assert not M.configure(path, '123456789012', str(folder), apply=True)['changed']


def test_missing_weights_or_pending_camera_restore_does_not_modify_config(tmp_path, monkeypatch):
    cfg = C.load(ROOT / 'params/omx3.yaml')
    path = tmp_path / 'omx3.yaml'; C.save(path, cfg); old = path.read_bytes()
    monkeypatch.setattr(R, 'device_lock', lambda _: nullcontext())
    from omx_manager import exposure
    monkeypatch.setattr(exposure, 'require_restored', lambda _: None)
    with pytest.raises((C.ConfigError, OSError)): M.configure(path, '123456789012', '/missing/model', apply=True)
    assert path.read_bytes() == old and not (tmp_path / 'backups').exists()
    monkeypatch.setattr(exposure, 'require_restored', Mock(side_effect=C.ConfigError('camera restore pending')))
    with pytest.raises(C.ConfigError, match='restore pending'): M.configure(path, '123456789012', apply=True)
    assert path.read_bytes() == old


def test_busy_device_does_not_change_config(tmp_path, monkeypatch):
    cfg = C.load(ROOT / 'params/omx3.yaml'); path = tmp_path / 'omx3.yaml'; C.save(path, cfg); old = path.read_bytes()
    monkeypatch.setattr(R, 'device_lock', Mock(side_effect=RuntimeError('device busy')))
    with pytest.raises(RuntimeError, match='busy'): M.configure(path, '123456789012', apply=True)
    assert path.read_bytes() == old


def test_templates_default_to_native_e_and_keep_temporary_f_as_explicit_option():
    cfg = C.load(ROOT / 'params/omx3.yaml'); four = C.load(ROOT / 'params/omx4.yaml')
    assert cfg['models']['active_profile'] == 'original'
    assert cfg['models']['profiles'][M.TEMP_PROFILE]['items']['E'] == C.profile(four)['items']['F']
    assert C.profile(cfg)['items']['E']['path'].endswith('warehouse_item_e_from_f50_act_300k/checkpoints/210000/pretrained_model')
    for c in (cfg, four):
        top = c['hardware']['cameras']['top']
        assert top['type'] == 'intelrealsense' and not top['use_depth'] and top['use_rgb']
    assert not cfg['unload']['inspection_enabled']


@pytest.mark.parametrize('inspection', [False, True])
@pytest.mark.parametrize('same_contract', [False, True])
def test_old_low_template_registration_is_disabled_even_on_repeat(inspection,same_contract):
    cfg = C.load(ROOT / 'params/omx3.yaml')
    if same_contract:
        cfg = M.prepare(cfg, '123456789012')
        cfg['home']['binding'] = 'new-registration-preserved'
    cfg['unload'].update(inspection_enabled=inspection, low_exposure_enabled=True,
                         inspection_exposure=81, vision_method='low_template', registration_enabled=True)
    C.validate(cfg)
    before = copy.deepcopy(cfg)
    updated = M.prepare(cfg, '123456789012')
    assert cfg == before
    assert not updated['unload']['registration_enabled']
    assert not updated['unload']['low_exposure_enabled']
    assert not updated['unload']['inspection_enabled']
    C.validate(updated)
    if same_contract: assert updated['home']['binding'] == 'new-registration-preserved'
