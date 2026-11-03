"""Recover existing, confirmed return regions after a small Home recapture.

Call under the device lease. Never infer a successful physical cycle, move a
motor, widen a region, or transfer a region to another model/item/robot.
"""
import copy
import hashlib
import json
from pathlib import Path
import time

from . import config as C
from .cycles import ARM, require_profile
from .workflow import registration_binding


def directory(cfg):
    return C.ROOT / 'state' / 'manager' / cfg['device_id'] / 'cycle_profiles'


def valid(cfg, item):
    try:
        require_profile(cfg, item)
        return True
    except C.ConfigError:
        return False


def remember(cfg):
    """Durably retain verified per-model profiles independently of mutable YAML."""
    if cfg['counting'].get('mode') != 'cycle':
        return []
    good = [item for item in C.profile(cfg)['items'] if valid(cfg, item)]
    if not good:
        return []
    try:
        C.require_home(cfg)
    except (C.ConfigError, OSError, ValueError, KeyError, TypeError):
        # Capture is also how an operator registers Home after a model change.
        # An unverifiable OLD snapshot must not prevent that explicit capture.
        return []
    written = []
    for item in good:
        payload = dict(schema_version=1, item=item, config=cfg)
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        path = directory(cfg) / item / (key + '.json')
        if not path.exists():
            C.write_json(path, payload)
        written.append(str(path))
    return written


def sources(cfg, config_path):
    """Bound lookup to local archives and YAML backups, newest first."""
    records = sorted(directory(cfg).glob('*/*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:64]
    for path in records:
        try:
            if path.is_symlink() or path.stat().st_size > 1000000:
                continue
            row = json.loads(path.read_text())
            if row.get('schema_version') == 1:
                C.validate(row['config'])
                yield row['config'], str(path)
        except (OSError, ValueError, KeyError, TypeError, C.yaml.YAMLError):
            continue
    path = Path(config_path)
    backups = sorted((path.parent / 'backups').glob(path.name + '.*.yaml'),
                     key=lambda p: p.stat().st_mtime, reverse=True)[:64]
    for backup in backups:
        try:
            if not backup.is_symlink() and backup.stat().st_size <= 1000000:
                yield C.load(backup), str(backup)
        except (OSError, ValueError, KeyError, TypeError, C.yaml.YAMLError):
            continue


def recover(cfg, candidates):
    """Pure config recovery; content hashes prove legacy model/hardware identity."""
    updated = copy.deepcopy(cfg)
    missing = [item for item in C.profile(cfg)['items'] if not valid(cfg, item)]
    restored = {}
    if not missing:
        return updated, restored
    C.require_home(cfg)
    checked = {}
    for source, origin in candidates:
        if not missing:
            break
        if (source['device_id'] != cfg['device_id'] or source['role'] != cfg['role']
                or source['hardware'] != cfg['hardware'] or source['counting'].get('mode') != 'cycle'
                or source['home']['target'] is None):
            continue
        for item in tuple(missing):
            row = source['counting']['profiles'].get(item)
            if (not row or row.get('source') != 'operator_confirmed_model_rollout'
                    or C.profile(source)['items'].get(item) != C.profile(cfg)['items'][item]
                    or not valid(source, item)):
                continue
            # A Home change inside the existing learned return window is
            # compatible. Never enlarge this window or authorize a far pose.
            if any(abs(source['home']['target'][joint] - cfg['home']['target'][joint]) >
                   min(row['tolerance'][joint], cfg['home']['tolerance'][joint]) for joint in ARM):
                continue
            if abs(source['home']['target']['gripper'] - cfg['home']['target']['gripper']) > cfg['home']['tolerance']['gripper']:
                continue
            key = json.dumps(dict(hardware=source['hardware'], model=C.profile(source)), sort_keys=True)
            if key not in checked:
                try:
                    checked[key] = C.binding(source)
                except (OSError, ValueError, KeyError, TypeError):
                    checked[key] = None
            if checked[key] is None or checked[key] != source['home'].get('binding'):
                continue
            trial = copy.deepcopy(updated)
            trial['counting']['profiles'][item] = dict(copy.deepcopy(row), binding=registration_binding(cfg))
            try:
                C.validate(trial)
                require_profile(trial, item)
            except (C.ConfigError, ValueError, KeyError, TypeError):
                continue
            updated = trial
            restored[item] = origin
            missing.remove(item)
    return updated, restored


def prepare(config_path, report=None):
    """Finish non-motion startup preparation before model load or job acceptance."""
    path = Path(config_path)
    initial = C.digest(path)
    cfg = C.load(path)
    if cfg['counting'].get('mode') != 'cycle':
        return cfg
    if all(valid(cfg, item) for item in C.profile(cfg)['items']):
        return cfg
    updated, restored = recover(cfg, sources(cfg, path))
    if restored:
        if C.digest(path) != initial:
            raise C.ConfigError('시작 준비 중 설정이 변경되어 덮어쓰지 않았습니다. 다시 시작하세요')
        C.require_home(updated)
        # Persist provenance first; interruption cannot lose the source data.
        remember(updated)
        C.write_json(directory(cfg) / ('recovery-' + str(time.time_ns()) + '.json'),
                     dict(at=time.time(), restored=restored, previous=cfg,
                          motor_writes=False, home_coordinates_preserved=True))
        C.save(path, updated)
        if report:
            report('[준비] 기존 정상 사이클 보정 자동 복원: ' + ', '.join(restored) + ' · 홈 좌표·판정 허용값 유지')
    return updated
