"""Real artifact hashes and config backups; no motors, cameras or inference."""
import copy
from pathlib import Path

import pytest

from omx_manager import config as C, cycle_registry as R
from omx_manager.cycles import require_profile
from omx_manager.workflow import registration_binding

ROOT = Path(__file__).resolve().parents[1]


def configured(tmp_path, monkeypatch, device='omx2'):
    cfg = C.load(ROOT / 'params' / (device + '.yaml'))
    cal = tmp_path / (cfg['hardware']['robot_id'] + '.json')
    C.write_json(cal, {j: dict(id=C.IDS[j], drive_mode=0, range_min=0, range_max=4095, homing_offset=0) for j in C.JOINTS})
    cfg['hardware']['calibration_file'] = str(cal)
    for item, model in C.profile(cfg)['items'].items():
        folder = tmp_path / item
        C.write_json(folder / 'config.json', dict(type='act',
            input_features={'observation.images.' + key: dict(shape=[3, cam['height'], cam['width']]) for key, cam in cfg['hardware']['cameras'].items()},
            output_features=dict(action=dict(shape=[6]))))
        (folder / 'model.safetensors').write_bytes(b'test fixture, never loaded')
        for name in ('policy_preprocessor.json', 'policy_postprocessor.json'):
            C.write_json(folder / name, dict(steps=[]))
        model['path'] = str(folder)
    cfg['home']['target'] = {j: 0. for j in C.JOINTS}
    cfg['home']['binding'] = C.binding(cfg)
    cfg['counting']['mode'] = 'cycle'
    cfg['counting']['profiles'] = {item: dict(binding=registration_binding(cfg),
        center={j: 0. for j in C.JOINTS}, tolerance={j: 4. for j in C.JOINTS[:-1]},
        leave_tolerance=12., confirmed_cycles=3, min_excursion=30., min_cycle_s=5.,
        source='operator_confirmed_model_rollout') for item in C.profile(cfg)['items']}
    monkeypatch.setattr(R, 'directory', lambda c: tmp_path / 'registry')
    path = tmp_path / 'site.yaml'
    C.save(path, cfg)
    return cfg, path


@pytest.mark.parametrize('device', ['omx1', 'omx2', 'omx3', 'omx4'])
def test_small_home_recapture_restores_exact_confirmed_profiles_on_start(tmp_path, monkeypatch, device):
    cfg, path = configured(tmp_path, monkeypatch, device)
    before = copy.deepcopy(cfg)
    cfg['home']['target']['wrist_flex'] = 2.
    C.save(path, cfg)
    logs = []
    result = R.prepare(path, report=logs.append)
    assert result['home']['target'] == cfg['home']['target']
    assert result['hardware'] == before['hardware'] and result['models'] == before['models']
    for item, expected in before['counting']['profiles'].items():
        assert require_profile(result, item) == dict(expected, binding=registration_binding(result))
    assert logs and '자동 복원' in logs[0]
    assert C.load(path) == result


def test_existing_item_remains_unchanged_when_another_item_is_missing(tmp_path, monkeypatch):
    cfg, path = configured(tmp_path, monkeypatch)
    del cfg['counting']['profiles']['D']
    C.save(path, cfg)
    result = R.prepare(path)
    assert result['counting']['profiles']['C'] == cfg['counting']['profiles']['C']
    require_profile(result, 'D')


@pytest.mark.parametrize('change', ['model', 'camera', 'port', 'far_home'])
def test_incompatible_evidence_is_not_rebound(tmp_path, monkeypatch, change):
    cfg, path = configured(tmp_path, monkeypatch)
    item = 'D'
    if change == 'model':
        C.profile(cfg)['items'][item]['task'] += ' changed model contract'
    elif change == 'camera':
        cfg['hardware']['cameras']['wrist']['index_or_path'] = '/different-camera'
    elif change == 'port':
        cfg['hardware']['port'] = '/different-arm'
    else:
        cfg['home']['target']['shoulder_pan'] = 20.
    cfg['home']['binding'] = C.binding(cfg)
    cfg['counting']['profiles'].pop(item)
    C.save(path, cfg)
    result = R.prepare(path)
    assert item not in result['counting']['profiles']
    with pytest.raises(C.ConfigError):
        require_profile(result, item)


def test_changed_weights_block_legacy_history_even_with_same_path(tmp_path, monkeypatch):
    cfg, path = configured(tmp_path, monkeypatch)
    C.path_value(C.profile(cfg)['items']['D']['path']).joinpath('model.safetensors').write_bytes(b'new weights')
    cfg['home']['binding'] = C.binding(cfg)
    cfg['counting']['profiles'] = {}
    C.save(path, cfg)
    assert R.prepare(path)['counting']['profiles'] == {}


def test_registry_survives_missing_yaml_backups_and_ignores_corrupt_archive(tmp_path, monkeypatch):
    cfg, path = configured(tmp_path, monkeypatch)
    R.remember(cfg)
    cfg['home']['target']['wrist_roll'] = 1.
    cfg['counting']['profiles'] = {}
    C.save(path, cfg)
    for backup in (tmp_path / 'backups').iterdir():
        backup.unlink()
    (tmp_path / 'registry' / 'C' / 'broken.json').write_text('{broken')
    result = R.prepare(path)
    require_profile(result, 'C')
    require_profile(result, 'D')


def test_no_history_never_fabricates_profile_and_valid_start_does_not_write(tmp_path, monkeypatch):
    cfg, path = configured(tmp_path, monkeypatch)
    raw = path.read_bytes()
    monkeypatch.setattr(R, 'recover', lambda *_: pytest.fail('valid config searched history'))
    assert R.prepare(path) == cfg and path.read_bytes() == raw
    monkeypatch.undo()
    monkeypatch.setattr(R, 'directory', lambda c: tmp_path / 'registry')
    cfg['counting']['profiles'] = {}
    C.save(path, cfg)
    for backup in (tmp_path / 'backups').iterdir():
        backup.unlink()
    raw = path.read_bytes()
    assert R.prepare(path)['counting']['profiles'] == {}
    assert path.read_bytes() == raw


def test_configuration_race_refuses_overwrite(tmp_path, monkeypatch):
    cfg, path = configured(tmp_path, monkeypatch)
    cfg['home']['target']['wrist_flex'] = 1.
    C.save(path, cfg)
    original = R.recover
    def race(*args):
        result = original(*args)
        path.write_text(path.read_text() + '\n# concurrent edit\n')
        return result
    monkeypatch.setattr(R, 'recover', race)
    with pytest.raises(C.ConfigError, match='덮어쓰지'):
        R.prepare(path)
    assert '# concurrent edit' in path.read_text()


def test_corrupt_yaml_backup_is_skipped(tmp_path, monkeypatch):
    cfg, path = configured(tmp_path, monkeypatch)
    cfg['home']['target']['wrist_flex'] = 1.
    C.save(path, cfg)
    (tmp_path / 'backups' / 'site.yaml.9999999999999999999.yaml').write_text('broken: [')
    require_profile(R.prepare(path), 'D')


def test_invalid_old_home_does_not_prevent_explicit_new_home_capture(tmp_path, monkeypatch):
    cfg, path = configured(tmp_path, monkeypatch)
    C.path_value(C.profile(cfg)['items']['D']['path']).joinpath('model.safetensors').write_bytes(b'new model')
    assert R.remember(cfg) == []
    assert not (tmp_path / 'registry').exists()


def test_supervisor_repairs_before_preflight_and_model_load(tmp_path, monkeypatch):
    from omx_manager import runtime
    cfg, path = configured(tmp_path, monkeypatch)
    cfg['home']['target']['wrist_flex'] = 1.
    C.save(path, cfg)
    events = []
    monkeypatch.setattr(runtime, 'state_dir', lambda _: tmp_path / 'runtime')
    supervisor = runtime.Supervisor(path, diagnostics=False, important_only=False)
    def preflight(actual):
        require_profile(actual, 'D')
        events.append('preflight_with_valid_profiles')
    def run_once(actual):
        require_profile(actual, 'D')
        events.append('model_load')
        supervisor.stop()
    monkeypatch.setattr(supervisor, 'preflight', preflight)
    monkeypatch.setattr(supervisor, 'run_once', run_once)
    supervisor.run()
    assert events == ['preflight_with_valid_profiles', 'model_load']
    require_profile(C.load(path), 'D')
