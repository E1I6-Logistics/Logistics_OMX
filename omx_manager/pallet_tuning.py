"""Human-labelled, bounded pallet tuning through the existing shared-frame worker."""
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
import zipfile

from . import pallet as P
from . import config as C
from .hardware import Lock


def settings_hash(spec):
    return hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()


def validate_parameters(cfg,item,parameters):
    if item not in P.ITEMS.get(cfg['device_id'],()):raise ValueError('이 로봇에 없는 품목입니다')
    if not isinstance(parameters,dict) or set(parameters)-set(P.TUNABLE):raise ValueError('허용된 판정값만 변경할 수 있습니다')
    updated=copy.deepcopy(cfg)
    updated['pallet_stock']=copy.deepcopy(P.options(cfg))
    updated['pallet_stock']['items'][item]['parameters']=dict(parameters)
    P.validate(updated)
    return updated


def apply_parameters(worker,item,payload):
    """Only the owner worker may persist/apply a validated, idle-time edit."""
    if not isinstance(payload,dict):raise ValueError('설정 저장 요청 오류')
    path=Path(os.environ.get('OMX_MANAGER_CONFIG','')).resolve()
    if not path.is_file() or str(path)!=payload.get('config_path'):raise ValueError('실행기와 view의 설정 파일이 다릅니다')
    with Lock(worker.directory/'pallet_config.lock'):
        cfg=C.load(path)
        if (settings_hash(worker.spec)!=payload.get('settings_hash') or P.options(cfg)!=worker.spec
                or P.signature(cfg,item)!=payload.get('binding')):raise ValueError('설정이 바뀌었습니다. 화면을 새로 열고 다시 촬영하세요')
        reference=P.load_reference(cfg,item)
        if hashlib.sha256(reference.tobytes()).hexdigest()!=payload.get('reference_hash'):raise ValueError('빈 기준이 변경되었습니다. 다시 확인하세요')
        updated=validate_parameters(cfg,item,payload.get('parameters'))
        reason=worker.gate()
        if reason:raise RuntimeError(reason)
        C.save(path,updated)  # existing atomic YAML writer + backup; no reference writes
        worker.cfg['pallet_stock']=updated['pallet_stock']
        worker.spec=P.options(worker.cfg);worker.inspector=P.Inspector(worker.cfg)
        worker.save(worker.inspector.unknown('품목별 판정값 적용 후 새 영상 확인 중'))
    return path


class Tuning:
    def __init__(self,cfg,path):
        self.cfg=cfg;self.path=Path(path).resolve() if path else None
        self.lock=threading.Lock();self.pending=None;self.samples=[]

    def config(self):
        if self.path is None:raise ValueError('설정 파일 경로가 없는 보기입니다')
        cfg=C.load(self.path)
        if cfg['device_id']!=self.cfg['device_id'] or cfg['device_id'] not in P.ITEMS:raise ValueError('OMX1·2 전용입니다')
        return cfg

    def info(self):
        cfg=self.config();s=P.options(cfg)
        return dict(device=cfg['device_id'],items={item:dict(roi=s['items'].get(item,{}).get('roi'),
            parameters={k:P.item_options(s,item)[k] for k in P.TUNABLE}) for item in P.ITEMS[cfg['device_id']]},
            samples=[dict(id=r['id'],item=r['item'],label=r['label'],at=r['at']) for r in self.samples])

    def capture(self,item):
        from .pallet_cli import request
        cfg=self.config()
        if item not in P.ITEMS[cfg['device_id']]:raise ValueError('품목을 선택하세요')
        def consume(reply):
            import numpy as np
            path=P.directory(cfg)/(cfg['device_id']+'_pallet_'+item+'_evidence.npz')
            if path.is_symlink() or not path.is_file() or path.stat().st_size>1000000:raise ValueError('진단 파일 오류')
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist())>600000:raise ValueError('진단 파일 크기 초과')
            with np.load(path,allow_pickle=False) as data:
                current,reference=data['current'].copy(),data['reference'].copy()
                spec=json.loads(str(data['options']))
                if str(data['robot_id'])!=cfg['device_id'] or str(data['item'])!=item:raise ValueError('진단 품목 불일치')
            if (current.shape!=reference.shape or current.dtype!=np.uint8 or reference.dtype!=np.uint8
                    or current.ndim!=3 or current.shape[2]!=3 or min(current.shape[:2])<12 or max(current.shape[:2])>160):raise ValueError('진단 ROI 형식 오류')
            meta=json.loads(path.with_name(path.name.replace('_evidence.npz','_diagnose.json')).read_text())
            if meta.get('frame_stamp')!=reply.get('frame_stamp'):raise ValueError('진단 영상이 교체되었습니다. 다시 촬영하세요')
            if any(r['session']==reply['session'] and r['stamp']==reply['frame_stamp'] for r in self.samples):raise ValueError('중복 영상입니다. 새 영상이 들어온 후 촬영하세요')
            return dict(id=uuid.uuid4().hex,item=item,current=current,reference=reference,spec=spec,
                at=time.time(),stamp=reply['frame_stamp'],session=reply['session'],binding=reply['binding'],
                settings_hash=reply['settings_hash'],reference_hash=hashlib.sha256(reference.tobytes()).hexdigest())
        # Copy before releasing the shared CLI lock: another diagnose cannot overwrite it.
        self.pending=request(cfg,'diagnose',item,consume=consume)
        return dict(id=self.pending['id'],item=item,at=self.pending['at'],comparison=P.compare(self.pending['current'],self.pending['reference'],self.pending['spec']))

    def label(self,identity,label):
        if label not in ('EMPTY','PRESENT'):raise ValueError('실제 비었음/물건 있음 중 선택하세요')
        row=self.pending
        if row is None or row['id']!=identity or time.time()-row['at']>120:raise ValueError('촬영이 만료됐습니다. 다시 촬영하세요')
        if sum(r['item']==row['item'] for r in self.samples)>=12:raise ValueError('품목당 최대 12장입니다. 자료 초기화 후 다시 수집하세요')
        import numpy as np
        cfg=self.config();folder=P.directory(cfg)/'pallet_tuning';folder.mkdir(parents=True,exist_ok=True)
        row=dict(row,label=label)
        slot=sum(r['item']==row['item'] for r in self.samples)
        path=folder/f'{cfg["device_id"]}_{row["item"]}_{slot:02d}.npz'
        # Bounded audit slots, separate from immutable pallet_refs (max 24 files).
        import tempfile
        fd,tmp=tempfile.mkstemp(dir=folder,suffix='.npz');os.close(fd)
        try:
            np.savez_compressed(tmp,current=row['current'],reference=row['reference'],
                options=np.array(json.dumps(row['spec'])),label=np.array(label),item=np.array(row['item']),
                robot_id=np.array(cfg['device_id']),metadata=np.array(json.dumps({k:v for k,v in row.items() if k not in ('current','reference','spec')})))
            os.replace(tmp,path)
        finally:Path(tmp).unlink(missing_ok=True)
        self.samples.append(row);self.pending=None
        return self.info()

    def evaluate(self,item,parameters):
        cfg=validate_parameters(self.config(),item,parameters)
        current_hash=settings_hash(P.options(self.config()))
        spec=P.item_options(P.options(cfg),item);rows=[]
        for sample in self.samples:
            if sample['item']!=item:continue
            if (sample['binding']!=P.signature(cfg,item) or sample['settings_hash']!=current_hash
                    or time.time()-sample['at']>1800):raise ValueError('촬영 이후 설정 변경 또는 30분 경과: 자료 초기화 후 다시 수집하세요')
            start=time.perf_counter();result=P.compare(sample['current'],sample['reference'],spec)
            rows.append(dict(id=sample['id'],label=sample['label'],candidate=result['candidate'],
                correct=sample['label']==result['candidate'],processing_ms=(time.perf_counter()-start)*1000,
                reason=result['reason'],strong=result.get('component_pixels'),weak=result.get('weak_component_pixels'),
                occlusion=result.get('grid_occlusion_pixels',0),ratio=result.get('changed_ratio')))
        enough=all(sum(r['label']==label for r in rows)>=3 for label in ('EMPTY','PRESENT'))
        return dict(rows=rows,can_save=bool(enough and all(r['correct'] for r in rows)),
            false_empty=sum(r['label']=='PRESENT' and r['candidate']=='EMPTY' for r in rows),
            message='비었음/있음 각각 3장 이상·전체 일치 필요')

    def suggest(self,item,parameters):
        report=self.evaluate(item,parameters)
        # Adjust ONLY final decision thresholds; masks and quality gates unchanged.
        # First two examples/class choose values, third+ examples remain a check.
        training=[]
        for label in ('EMPTY','PRESENT'):
            subset=[r for r in report['rows'] if r['label']==label]
            if len(subset)<3:raise ValueError('비었음·있음 각각 3장을 먼저 등록하세요')
            training.extend(subset[:2])
        empty=[r for r in training if r['label']=='EMPTY']
        if any(r['strong'] is None or r['weak'] is None or r['ratio'] is None for r in training):raise ValueError('조명·기준 품질 문제는 임계값으로 해결할 수 없습니다')
        proposed=dict(parameters)
        proposed['min_component_pixels']=max(parameters['min_component_pixels'],int(max(max(r['strong'],r['occlusion']) for r in empty))+1)
        proposed['empty_component_pixels']=max(4,int(max(max(r['weak'],r['occlusion']) for r in empty))+1)
        proposed['empty_ratio']=round(max(r['ratio'] for r in empty)+.0005,6)
        validate_parameters(self.config(),item,proposed)
        return dict(parameters=proposed,report=self.evaluate(item,proposed),
                    message='각 상태 첫 2장으로 제안, 나머지 사진으로 추가 확인. 현장 정확도 보장은 아닙니다')

    def apply(self,item,parameters):
        from .pallet_cli import request
        cfg=self.config();report=self.evaluate(item,parameters)
        if not report['can_save']:raise ValueError('비었음·있음 각각 3장 이상이 모두 맞아야 저장됩니다')
        samples=[r for r in self.samples if r['item']==item];sample=samples[-1]
        if len({r['session'] for r in samples})!=1 or len({r['reference_hash'] for r in samples})!=1:raise ValueError('실행기 또는 빈 기준이 바뀌었습니다. 다시 수집하세요')
        stock=json.loads((P.directory(cfg)/'pallet_stock.json').read_text())
        if stock['session']!=sample['session']:raise ValueError('로봇 실행기가 재시작됐습니다. 다시 수집하세요')
        reply=request(cfg,'tune',item,payload=dict(config_path=str(self.path),parameters=parameters,
            settings_hash=sample['settings_hash'],binding=sample['binding'],reference_hash=sample['reference_hash']))
        self.samples=[];self.pending=None
        return dict(message='저장·즉시 적용 완료. 모델 재시작 없음. 다른 품목 자료는 다시 수집하세요',path=reply['path'])

    def image(self,identity):
        import cv2
        import numpy as np
        row=next((r for r in ([self.pending] if self.pending else [])+self.samples if r['id']==identity),None)
        if row is None:raise ValueError('사진이 없습니다')
        image=np.hstack((row['reference'],row['current']))
        from .viewer import label_image
        image=label_image(cv2.cvtColor(image,cv2.COLOR_RGB2BGR),self.cfg['device_id'],row['item']+' | EMPTY REF / CAPTURED')
        ok,encoded=cv2.imencode('.png',image)
        if not ok:raise ValueError('사진 인코딩 실패')
        return encoded.tobytes()

    def dispatch(self,body):
        if not self.lock.acquire(blocking=False):raise RuntimeError('다른 조정 요청 처리 중입니다')
        try:
            action=body.get('action');item=body.get('item')
            if action=='capture':return self.capture(item)
            if action=='label':return self.label(body.get('id'),body.get('label'))
            if action=='evaluate':return self.evaluate(item,body.get('parameters'))
            if action=='suggest':return self.suggest(item,body.get('parameters'))
            if action=='apply' and body.get('confirm') is True:return self.apply(item,body.get('parameters'))
            if action=='clear':self.samples=[];self.pending=None;return self.info()
            raise ValueError('지원하지 않는 조정 요청입니다')
        finally:self.lock.release()
