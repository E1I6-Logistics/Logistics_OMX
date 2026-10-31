"""Collect labeled RGB evidence without running a policy or changing references."""
import copy
import getpass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
import zipfile

from .config import ROOT, ConfigError, path_value, write_json

SCENES=(('empty_before','EMPTY','바구니를 완전히 비우고 손을 떼세요'),
        ('occupied_center','OCCUPIED','문제였던 물건 1개를 카메라에 보이는 바구니 중앙에 놓고 손을 떼세요'),
        ('occupied_edge','OCCUPIED','같은 물건을 카메라에 보이는 바구니 벽 쪽으로 옮기고 손을 떼세요'),
        ('empty_after','EMPTY','물건을 모두 빼고 손을 떼세요'))
STATE_FILES=('metrics.json','joint_errors.json','home_motion.json','jobs.json','inventory.json',
             'camera_status.json','recovery_motion.json','recovery_wait.json','last_stop.json',
             'basket_failure.json','basket_watch.json','basket_status.json','workstation_status.json',
             'camera_exposure_last.json','camera_exposure_pending.json')


def redacted(value):
    if isinstance(value,dict):
        return {k:('[REDACTED]' if re.search(r'password|passwd|secret|token|api_key',str(k),re.I) else redacted(v)) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [redacted(v) for v in value]
    return value


def copy_file(source,destination,manifest,limit=8*1024*1024):
    source=Path(source)
    if not source.is_file():return
    if source.stat().st_size>limit:
        manifest.setdefault('skipped',[]).append(str(source)+' (size limit)');return
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    if source.suffix.lower()=='.json':
        try:write_json(destination,redacted(json.loads(source.read_text())),durable=False)
        except (ValueError,UnicodeError):
            manifest.setdefault('skipped',[]).append(str(source)+' (invalid JSON)')
    else:shutil.copyfile(source,destination)


def snapshot_existing(cfg,folder,manifest):
    from .basket import options
    state=ROOT/'state'/'manager'/cfg['device_id']
    for name in STATE_FILES:copy_file(state/name,folder/'state'/name,manifest)
    for name in ('basket_failure.png','basket_compare.png','basket_pose.png','basket_view.png','basket_low.png'):
        copy_file(state/name,folder/'previous_images'/name,manifest)
    spec=options(cfg)
    if spec.get('reference'):copy_file(path_value(spec['reference']),folder/'reference.npz',manifest)
    copy_file(path_value(cfg['hardware']['calibration_file']),folder/'motor_calibration.json',manifest)
    for name in ('important.log','important.log.1','full.log','full.log.1'):
        copy_file(ROOT/'logs'/cfg['device_id']/name,folder/'logs'/name,manifest)
    from .config import profile
    for item,model in profile(cfg)['items'].items():
        directory=path_value(model['path'])
        manifest.setdefault('models',{})[item]=dict(path=str(directory),files=[])
        if not directory.is_dir():continue
        for f in sorted(directory.iterdir()):
            if not f.is_file():continue
            manifest['models'][item]['files'].append(dict(name=f.name,size=f.stat().st_size,mtime=f.stat().st_mtime))
            # Include metadata / normalization only, never the large weights.
            if f.suffix=='.json' or f.suffix=='.safetensors' and 'processor' in f.name:
                copy_file(f,folder/'model_metadata'/item/f.name,manifest,2*1024*1024)


def system_info():
    result={}
    result['packages']={}
    for package in ('torch','opencv-python','pyrealsense2','draccus','dynamixel-sdk','paho-mqtt'):
        try:result['packages'][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:result['packages'][package]='not found'
    commands={'git_commit':['git','rev-parse','HEAD'],'git_status':['git','status','--short','--branch'],
              'ip':['ip','-j','-4','addr','show','scope','global'],'usb':['lsusb','-t'],
              'throttled':['vcgencmd','get_throttled'],'temperature':['vcgencmd','measure_temp'],
              'kernel':['journalctl','-k','-n','100','--no-pager']}
    for name,args in commands.items():
        try:
            row=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=3)
            result[name]=dict(exit=row.returncode,stdout=row.stdout[-32768:],stderr=row.stderr[-4096:])
        except (OSError,subprocess.TimeoutExpired) as exc:result[name]=dict(error=str(exc))
    return result


def make_cameras(cfg):
    from .commissioning import camera_for
    cameras={}
    for name in cfg['hardware']['cameras']:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',name):raise ConfigError('Invalid camera input name')
        selected=copy.deepcopy(cfg);selected['unload']['camera']=name
        cameras[name]=camera_for(selected)
    return cameras


def write_png(path,frame):
    import cv2
    path.parent.mkdir(parents=True,exist_ok=True)
    if not cv2.imwrite(str(path),cv2.cvtColor(frame,cv2.COLOR_RGB2BGR),[cv2.IMWRITE_PNG_COMPRESSION,1]):
        raise OSError('Cannot save RGB evidence: '+str(path))


def record_frames(camera,directory,*,count,spec,clock,sleep,inspector=None,controls=None):
    from .basket import read_camera
    rows=[];last=None;deadline=clock()+max(5.,count*.5)
    try:
        while len(rows)<count:
            if clock()>deadline:raise TimeoutError('Fresh distinct camera frames unavailable')
            actual=controls.snapshot() if controls is not None else None
            try:frame,stamp=read_camera(camera,spec['max_frame_age_s'])
            except TimeoutError:sleep(.03);continue
            if last is not None and stamp<=last:sleep(.03);continue
            last=stamp;now=clock()
            row=dict(index=len(rows),frame_monotonic=stamp,observed_monotonic=now,at=time.time(),
                     frame_age_ms=(now-stamp)*1000,shape=list(frame.shape),controls=actual)
            # Keep every sample, regardless of the detector's answer.
            write_png(directory/f'{len(rows):03d}.png',frame.copy())
            if inspector is not None:
                try:
                    inspector.update(frame,now,stamp);row['prediction']=inspector.snapshot(time.time())
                except Exception as exc:row['prediction_error']=str(exc)
            rows.append(row);sleep(.1)
    finally:write_json(directory/'frames.json',rows,durable=False)
    return rows


def pack(folder,device):
    archive=folder.with_suffix('.zip');temporary=archive.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary,'w') as z:
        for file in sorted(folder.rglob('*')):
            if not file.is_file() or file.is_symlink():continue
            compression=zipfile.ZIP_STORED if file.suffix in ('.png','.npz','.safetensors') else zipfile.ZIP_DEFLATED
            z.write(file,str(file.relative_to(folder)),compress_type=compression,compresslevel=1 if compression==zipfile.ZIP_DEFLATED else None)
    os.replace(temporary,archive)
    latest=folder.parent/(device+'_evidence.zip');link=latest.with_suffix('.zip.tmp')
    link.unlink(missing_ok=True);link.symlink_to(archive.name);os.replace(link,latest)
    return latest


def collect(cfg,config_path,device,*,input_fn=input,cameras=None,clock=time.perf_counter,sleep=time.sleep,out=None):
    """Caller holds device and motor-port locks. No motion / configuration writes."""
    from .basket import options,BasketInspector
    from .exposure import Session,require_restored,journal_path
    spec=options(cfg)
    if cfg['role']!='unload' or not spec['low_exposure_enabled']:
        raise ConfigError('RGB low-exposure evidence collection is for configured OMX3/4')
    identifier=cfg['device_id']
    parent=Path(out) if out is not None else ROOT/'state'/'diagnostics'
    folder=parent/(identifier+'_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
    folder.mkdir(parents=True)
    manifest=dict(version=1,device=identifier,status='INCOMPLETE',motor_writes=False,policy_executed=False,
                  reference_changed=False,config_changed=False,config_path=str(Path(config_path).resolve()),scenes=[])
    write_json(folder/'config.json',redacted(cfg),durable=False)
    config_digest=hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
    reference=path_value(spec['reference']) if spec.get('reference') else None
    def reference_digest():
        return hashlib.sha256(reference.read_bytes()).hexdigest() if reference is not None and reference.is_file() else None
    reference_before=reference_digest();manifest['reference_sha256_before']=reference_before
    opened=[];error=None
    try:
        snapshot_existing(cfg,folder,manifest)
        manifest['system']=system_info()
        manifest['joints_before']=device.read()
        require_restored(cfg)
        try:inspector=BasketInspector(cfg)
        except Exception as exc:inspector=None;manifest['inspector_error']=str(exc)
        cameras=make_cameras(cfg) if cameras is None else cameras
        for name,camera in cameras.items():
            print('[수집] 카메라 연결: '+name,flush=True);opened.append(camera);camera.connect()
        top=cameras[spec['camera']]
        for index,(name,truth,prompt) in enumerate(SCENES,1):
            answer=input_fn(f'[{index}/4] {prompt}. Enter: 촬영 / q: 종료 > ').strip().lower()
            if answer=='q':raise InterruptedError('Operator cancelled evidence collection')
            scene=dict(name=name,operator_ground_truth=truth,joints=device.read(),normal={})
            manifest['scenes'].append(scene)
            scene_folder=folder/'scenes'/name
            for camera_name,camera in cameras.items():
                scene['normal'][camera_name]=record_frames(camera,scene_folder/'normal'/camera_name,
                    count=2,spec=spec,clock=clock,sleep=sleep)
            if inspector is not None:inspector.reset()
            with Session(top,cfg,target=inspector.exposure_profile if inspector else None,allow_scene_change=True) as session:
                scene['normal_controls_before_inspection']=dict(session.original)
                scene['inspection_profile']=session.profile()
                scene['inspection_focus']=session.record.get('focus_locked')
                scene['original_focus']=session.record.get('focus_original')
                scene['low']=record_frames(top,scene_folder/'low'/spec['camera'],count=8,spec=spec,
                    clock=clock,sleep=sleep,inspector=inspector,controls=session.adapter)
            scene['timings']=session.timing;scene['restore_verified']=not journal_path(cfg).exists()
            write_json(folder/'manifest.json',manifest,durable=False)
            print('[수집] '+name+' 저장·원래 노출 복원 완료. 기준 변경 없음.',flush=True)
        manifest['status']='COMPLETE'
    except BaseException as exc:
        error=exc;manifest.update(status='INTERRUPTED' if isinstance(exc,(KeyboardInterrupt,InterruptedError)) else 'ERROR',error=repr(exc))
    finally:
        for camera in reversed(opened):
            try:
                if camera.is_connected or getattr(camera,'thread',None) is not None:camera.disconnect()
            except Exception as exc:manifest.setdefault('cleanup_errors',[]).append(str(exc))
        manifest['exposure_restore_pending']=journal_path(cfg).exists()
        manifest['reference_sha256_after']=reference_digest()
        manifest['reference_changed']=manifest['reference_sha256_after']!=reference_before
        manifest['config_changed']=hashlib.sha256(Path(config_path).read_bytes()).hexdigest()!=config_digest
        if error is None and (manifest.get('cleanup_errors') or manifest['exposure_restore_pending'] or manifest['reference_changed'] or manifest['config_changed']):
            error=RuntimeError('Collection cleanup or source preservation verification failed')
            manifest.update(status='ERROR',error=str(error))
        manifest['collected_at']=time.time()
        write_json(folder/'manifest.json',manifest,durable=False)
        # Preserve the final recovery journal as well as the pre-collection one.
        for name in ('camera_exposure_last.json','camera_exposure_pending.json'):
            copy_file(ROOT/'state'/'manager'/identifier/name,folder/'after'/name,manifest)
        archive=pack(folder,identifier)
        print('[수집] ZIP: '+str(archive),flush=True)
        ip='192.168.0.153' if identifier=='omx3' else '192.168.0.154'
        try:
            addresses=json.loads(manifest['system']['ip']['stdout'])
            ip=next(a['local'] for interface in addresses for a in interface.get('addr_info',[]) if a.get('local','').startswith('192.168.0.'))
        except (KeyError,ValueError,TypeError,StopIteration):pass
        print('[Windows PowerShell] scp '+getpass.getuser()+'@'+ip+':'+str(archive)+' "$env:USERPROFILE\\Downloads\\'+identifier+'_evidence.zip"',flush=True)
    if error is not None:raise RuntimeError('수집 일부 실패. ZIP은 저장됨: '+str(archive)+' / '+str(error)) from error
    return dict(status=manifest['status'],archive=str(archive),scenes=len(manifest['scenes']))
