"""OMX3 한 대를 OMX4 모델 + RealSense RGB 입력으로 전환. 모터 쓰기 없음."""
import argparse
import copy
import json
from pathlib import Path

from . import config as C

TEMP_PROFILE = 'omx4_temporary'


def resolve_serial(value):
    if value != 'auto':
        if not isinstance(value, str) or not value.isascii() or not value.isdigit():
            raise C.ConfigError('RealSense 실제 숫자 시리얼을 입력하세요. 자동 검색은 auto입니다')
        return value
    import pyrealsense2 as rs
    serials = [d.get_info(rs.camera_info.serial_number) for d in rs.context().query_devices()]
    if len(serials) != 1:
        raise C.ConfigError(f'RealSense {len(serials)}대 검색됨. 한 대를 연결하거나 --serial 실제시리얼로 지정하세요')
    return resolve_serial(serials[0])


def prepare(cfg, serial, model_path=None):
    if cfg['device_id'] != 'omx3':
        raise C.ConfigError('이 전환은 OMX3 전용입니다. OMX1·2·4 설정은 변경하지 않습니다')
    serial = resolve_serial(serial) if serial != 'auto' else None
    if serial is None:
        raise C.ConfigError('prepare에는 자동 검색이 끝난 실제 시리얼이 필요합니다')
    source = C.load(C.ROOT / 'params/omx4.yaml')
    updated = copy.deepcopy(cfg)
    model = copy.deepcopy(C.profile(source)['items']['F'])
    if model_path is not None:
        C.path_value(model_path)
        model['path'] = model_path
    temporary = dict(runtime='lerobot_pytorch', precision='fp32', items={'E': model})
    top = dict(type='intelrealsense', serial_number_or_name=serial, width=640,
               height=480, fps=30, use_rgb=True, use_depth=False, warmup_s=5)
    wrist = copy.deepcopy(source['hardware']['cameras']['wrist'])
    existing_wrist = cfg['hardware']['cameras'].get('wrist', {})
    if existing_wrist.get('type') == 'opencv' and existing_wrist.get('index_or_path'):
        wrist['index_or_path'] = existing_wrist['index_or_path']
    cameras = dict(top=top, wrist=wrist)
    changed_contract = C.profile(cfg) != temporary or cfg['hardware']['cameras'] != cameras
    updated['models']['profiles'][TEMP_PROFILE] = temporary
    updated['models']['active_profile'] = TEMP_PROFILE
    updated['hardware']['cameras'] = cameras
    updated.setdefault('unload', {}).update(inspection_enabled=False, low_exposure_enabled=False,
                                           inspection_exposure=None, vision_method='rgb_roi', registration_enabled=False)
    updated['counting']['mode'] = 'cycle'
    if changed_contract:
        # Retain the physical Home values and this arm's motor calibration.
        # A new model/camera cannot inherit another model's validation hash.
        updated['home']['binding'] = None
        updated['counting']['profiles'] = {}
        updated.setdefault('workflow', {})['items'] = {}
        updated['recovery']['routes'] = {}
        updated['unload'].update(reference=None, reference_binding=None, roi=None, roi_polygon=None,
                                 registration_allow_partial=False, registration_visible_confirmed=False,
                                 full_view_confirmed=False)
    C.validate(updated)
    return updated


def configure(path, serial='auto', model_path=None, apply=False):
    from .runtime import device_lock
    from .exposure import require_restored
    cfg = C.load(path)
    if cfg['device_id'] != 'omx3':
        raise C.ConfigError('OMX3 설정 파일만 지정하세요')
    with device_lock(cfg):
        require_restored(cfg)
        updated = prepare(cfg, resolve_serial(serial), model_path)
        # Validate local weights/processors and the ORIGINAL OMX3 calibration
        # before writing YAML. Never download a model or open a camera stream.
        C.check_files(updated)
        changed = updated != cfg
        if apply and changed:
            C.save(path, updated)  # Creates a complete original-YAML backup.
    result = dict(applied=bool(apply and changed), changed=changed, device='omx3', external_item='E',
                  profile=TEMP_PROFILE, model=C.profile(updated)['items']['E']['path'],
                  realsense_serial=updated['hardware']['cameras']['top']['serial_number_or_name'],
                  inspection_enabled=False, motor_writes=False,
                  home_registration_required=updated['home']['binding'] is None,
                  backup_directory=str(Path(path).parent / 'backups'))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--serial', default='auto', help='OMX3에 연결한 RealSense 실제 시리얼 또는 auto')
    parser.add_argument('--model-path', help='OMX4 F 230000 pretrained_model의 현장 경로 (생략 시 저장소 기본값)')
    parser.add_argument('--apply', action='store_true', help='생략하면 파일 확인·변경 계획만 출력')
    args = parser.parse_args(argv)
    try:
        result = configure(args.config, args.serial, args.model_path, args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.apply:
            print('OMX3 전환 설정 완료. 자동 시작하지 않습니다. 모델 초기 자세 확인 후 omx_capture → omx_cycle_calibrate E → omx_workflow 순서로 확인하세요')
        else:
            print('확인만 완료. 실제 설정 저장은 --apply를 추가하세요')
    except (Exception, KeyboardInterrupt) as exc:
        print('[ERROR] OMX3 전환 실패:', str(exc))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
