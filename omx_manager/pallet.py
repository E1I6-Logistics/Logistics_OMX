"""Fixed pallet RGB presence, no item counts, model inference or camera controls."""
import hashlib
import json
import time
from pathlib import Path
from .config import ConfigError,number,only_keys,write_json,ROOT

DEFAULTS=dict(enabled=False,camera='front',interval_s=5.,stable_samples=3,max_frame_age_s=.5,
              pixel_threshold=20.,weak_pixel_threshold=8.,min_component_pixels=20,empty_ratio=.003,max_color_offset=18.,
              precision_enabled=False,adaptive_color_limit=40.,alignment_max_px=3,reference_slack_px=2,min_object_width_px=3,empty_component_pixels=None,items={})
ITEMS={'omx1':('A','B'),'omx2':('C','D')}


def options(cfg):return dict(DEFAULTS,**cfg.get('pallet_stock',{}))


TUNABLE = ('precision_enabled','pixel_threshold','weak_pixel_threshold','min_component_pixels',
           'empty_component_pixels','empty_ratio','max_color_offset','adaptive_color_limit',
           'alignment_max_px','reference_slack_px','min_object_width_px')


def item_options(spec,item):
    return dict(spec,**spec['items'].get(item,{}).get('parameters',{}))


def classify(strong,weak,occlusion,ratio,s):
    empty_limit=s.get('empty_component_pixels')
    if empty_limit is None:empty_limit=max(4,s['min_component_pixels']//3)
    if strong>=s['min_component_pixels'] or occlusion>=s['min_component_pixels']:return 'PRESENT'
    if weak<empty_limit and occlusion<empty_limit and ratio<=s['empty_ratio']:return 'EMPTY'
    return 'UNKNOWN'


def validate(cfg):
    section=cfg.get('pallet_stock',{})
    if not isinstance(section,dict):raise ConfigError('pallet_stock는 설정 목록이어야 합니다')
    only_keys(section,' '.join(DEFAULTS),'pallet_stock');s=options(cfg)
    if type(s['enabled']) is not bool:raise ConfigError('재고 검사 enabled는 true/false여야 합니다')
    if s['enabled'] and cfg['device_id'] not in ITEMS:raise ConfigError('팔레트 검사는 OMX1·2 전용입니다')
    if not isinstance(s['camera'],str) or not s['camera']:raise ConfigError('팔레트 카메라 키는 문자열이어야 합니다')
    if s['enabled'] and s['camera'] not in cfg['hardware']['cameras']:raise ConfigError('팔레트 검사 카메라가 없습니다')
    for key,lo,hi in (('interval_s',2,300),('max_frame_age_s',.1,2),('pixel_threshold',5,80),('weak_pixel_threshold',2,40),('empty_ratio',0,.02),('max_color_offset',0,30)):
        number(s[key],'pallet_stock.'+key,lo,hi)
    if s['empty_component_pixels'] is not None and (type(s['empty_component_pixels']) is not int or not 1<=s['empty_component_pixels']<=200):raise ConfigError('빈 상태 잔여 연결 영역 허용값은 1~200 또는 null입니다')
    if type(s['precision_enabled']) is not bool:raise ConfigError('팔레트 정밀 비교 사용 여부는 true/false여야 합니다')
    number(s['adaptive_color_limit'],'pallet_stock.adaptive_color_limit',0,60)
    for key,lo,hi in (('alignment_max_px',0,5),('reference_slack_px',0,3),('min_object_width_px',1,5)):
        if type(s[key]) is not int or not lo<=s[key]<=hi:raise ConfigError('잘못된 정밀 비교 설정: '+key)
    if s['min_object_width_px']%2!=1:raise ConfigError('최소 물체 폭 필터는 1/3/5 중 하나입니다')
    if s['weak_pixel_threshold']>=s['pixel_threshold']:raise ConfigError('약한 변화 기준은 강한 변화 기준보다 작아야 합니다')
    for key,lo,hi in (('stable_samples',2,10),('min_component_pixels',4,500)):
        if type(s[key]) is not int or not lo<=s[key]<=hi:raise ConfigError('잘못된 팔레트 검사 설정: '+key)
    if not isinstance(s['items'],dict) or set(s['items'])-set(ITEMS.get(cfg['device_id'],())):raise ConfigError('팔레트 품목은 OMX1=A/B, OMX2=C/D만 허용합니다')
    for item,row in s['items'].items():
        if not isinstance(row,dict):raise ConfigError('팔레트 품목 설정 오류')
        only_keys(row,'roi parameters','팔레트 품목')
        parameters=row.get('parameters',{})
        if not isinstance(parameters,dict):raise ConfigError('품목별 parameters는 설정 목록이어야 합니다')
        only_keys(parameters,' '.join(TUNABLE),'품목별 판정값')
        if parameters:
            # Validate merged values with exactly the same global bounds.
            merged=dict(s,**parameters,items={})
            validate(dict(cfg,pallet_stock=merged))
        roi=row.get('roi')
        if roi is not None and (not isinstance(roi,list) or len(roi)!=4 or any(type(v) is not int for v in roi)
                or min(roi[:2])<0 or min(roi[2:])<12):raise ConfigError('ROI는 [x,y,너비,높이] 정수이며 크기는 최소 12입니다')
        if roi is not None and s['camera'] in cfg['hardware']['cameras']:
            cam=cfg['hardware']['cameras'][s['camera']]
            if roi[0]+roi[2]>cam['width'] or roi[1]+roi[3]>cam['height']:raise ConfigError('팔레트 ROI가 카메라 영상 밖입니다')


def directory(cfg):return ROOT/'state'/'manager'/cfg['device_id']


def signature(cfg,item):
    s=options(cfg)
    material=dict(device=cfg['device_id'],item=item,camera=s['camera'],hardware=cfg['hardware']['cameras'][s['camera']],
                  roi=s['items'][item]['roi'],home=cfg['home']['target'],home_binding=cfg['home'].get('binding'))
    return hashlib.sha256(json.dumps(material,sort_keys=True).encode()).hexdigest()


def crop(frame,roi):
    import cv2
    import numpy as np
    if roi is None:raise ConfigError('팔레트 ROI 미등록')
    if not isinstance(frame,np.ndarray) or frame.ndim!=3 or frame.shape[2]!=3 or frame.dtype!=np.uint8:raise ConfigError('RGB 영상 형식 오류')
    x,y,w,h=roi
    if x+w>frame.shape[1] or y+h>frame.shape[0]:raise ConfigError('팔레트 ROI가 현재 영상 밖입니다')
    image=frame[y:y+h,x:x+w].copy()
    if max(w,h)>160:image=cv2.resize(image,(max(1,round(w*160/max(w,h))),max(1,round(h*160/max(w,h)))),interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(image,(3,3),0)


def save_reference(cfg,item,frames):
    import os,tempfile
    import numpy as np
    s=options(cfg);camera=cfg['hardware']['cameras'][s['camera']]
    if any(frame.shape!=(camera['height'],camera['width'],3) for frame in frames):raise ConfigError('기준 촬영 영상 해상도가 카메라 설정과 다릅니다')
    images=[crop(frame,s['items'][item]['roi']) for frame in frames]
    if len(images)<3:raise ConfigError('서로 다른 새 영상 3장 이상이 필요합니다')
    if max(float(np.mean(np.abs(a.astype(float)-images[0]))) for a in images[1:])>3:raise ConfigError('빈 기준 촬영 중 영상이 움직였습니다. 손을 뗀 후 다시 시도하세요')
    image=np.median(np.stack(images),axis=0).astype(np.uint8)
    if float(image.std())<3:raise ConfigError('빈 기준 영상의 명암이 부족합니다. ROI와 카메라를 확인하세요')
    path=directory(cfg)/'pallet_refs'/(item+'.npz');path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,suffix='.npz');os.close(fd)
    try:
        np.savez_compressed(tmp,image=image,binding=np.array(signature(cfg,item)))
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)
    return path


def load_reference(cfg,item):
    import numpy as np
    path=directory(cfg)/'pallet_refs'/(item+'.npz')
    if not path.is_file() or path.is_symlink() or path.stat().st_size>300000:raise ConfigError('빈 팔레트 기준 미등록 또는 파일 오류')
    with np.load(path,allow_pickle=False) as data:
        if str(data['binding'])!=signature(cfg,item):raise ConfigError('빈 기준의 ROI·카메라·홈 설정이 현재와 다릅니다')
        image=data['image']
        if image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=3 or max(image.shape[:2])>160:raise ConfigError('빈 기준 영상 형식 오류')
        return image.copy()


def compare(current,reference,s):
    import cv2
    import numpy as np
    if current.shape!=reference.shape:raise ConfigError('빈 기준 영상 크기 불일치')
    if s.get('precision_enabled',False):
        from .pallet_vision import compare as precision_compare
        return precision_compare(current,reference,s)
    a=current.astype(np.float32);b=reference.astype(np.float32)
    offset=np.median(a-b,axis=(0,1));clipped=float(np.mean(current.min(axis=2)>=250))
    reference_clipped=float(np.mean(reference.min(axis=2)>=250))
    clipped_limit=max(.45,reference_clipped+.1)
    limit=s['max_color_offset']
    flags=[]
    if max(abs(offset))>limit:flags.append('COLOR_OFFSET_LIMIT')
    if clipped>clipped_limit:flags.append('OVEREXPOSED')
    metadata=dict(color_offset=offset.tolist(),color_offset_limit=limit,
                  clipped_ratio=clipped,reference_clipped_ratio=reference_clipped,
                  clipped_limit=clipped_limit,quality_flags=flags)
    if flags:
        reason='과노출 픽셀 비율 증가' if 'OVEREXPOSED' in flags else '색상 변화가 보정 한도 초과'
        return dict(metadata,candidate='UNKNOWN',reason=reason,features_computed=False)
    # Correct only a bounded per-channel median shift in this ROI copy.
    # Local connected change is STILL required; offset alone is not an object.
    # One-pixel local tolerance absorbs static-grid jitter without image warping.
    hi=cv2.dilate(reference,np.ones((3,3),np.uint8)).astype(float)
    lo=cv2.erode(reference,np.ones((3,3),np.uint8)).astype(float)
    corrected=a-offset
    diff=np.max(np.maximum(lo-corrected,corrected-hi),axis=2)
    def component(threshold):
        mask=(diff>threshold).astype(np.uint8);mask[[0,-1],:]=0;mask[:,[0,-1]]=0
        n,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
        return int(stats[1:,cv2.CC_STAT_AREA].max()) if n>1 else 0
    strong=component(s['pixel_threshold']);weak=component(s['weak_pixel_threshold'])
    ratio=float(np.mean(diff>s['weak_pixel_threshold']))
    candidate=classify(strong,weak,0,ratio,s)
    reason={'PRESENT':'빈 기준과 물체 크기 변화 확인','EMPTY':'빈 기준과 일치','UNKNOWN':'변화가 작거나 물건 여부 불명확'}[candidate]
    return dict(metadata,candidate=candidate,reason=reason,component_pixels=strong,weak_component_pixels=weak,changed_ratio=ratio,features_computed=True)


class Inspector:
    def __init__(self,cfg):self.cfg=cfg;self.spec=options(cfg);self.state={};self.last_stamp=None;self.last_time=None
    def unknown(self,reason):
        self.state={};self.last_stamp=None;self.last_time=None
        return {item:dict(state='UNKNOWN',reason=reason,stable_samples=0) for item in ITEMS.get(self.cfg['device_id'],())}
    def update(self,frame,stamp,now):
        camera=self.cfg['hardware']['cameras'][self.spec['camera']]
        if not hasattr(frame,'shape') or frame.shape!=(camera['height'],camera['width'],3):return self.unknown('카메라 영상 해상도 불일치')
        if not 0<=now-stamp<=self.spec['max_frame_age_s'] or self.last_stamp is not None and stamp<=self.last_stamp:
            return self.unknown('영상이 오래되었거나 중복됨')
        if self.last_time is not None and now-self.last_time>self.spec['interval_s']*2.5:self.state={}
        self.last_stamp=stamp;self.last_time=now;rows={}
        for item in ITEMS[self.cfg['device_id']]:
            started=time.perf_counter()
            try:
                roi=self.spec['items'].get(item,{}).get('roi');current=crop(frame,roi)
                result=compare(current,load_reference(self.cfg,item),item_options(self.spec,item))
            except (OSError,ValueError,KeyError) as exc:result=dict(candidate='UNKNOWN',reason=str(exc))
            result['processing_ms']=(time.perf_counter()-started)*1000
            candidate=result['candidate'];prior,count=self.state.get(item,(None,0))
            count=count+1 if candidate==prior and candidate!='UNKNOWN' else 1 if candidate!='UNKNOWN' else 0
            self.state[item]=(candidate,count)
            rows[item]=dict(result,state=candidate if count>=self.spec['stable_samples'] else 'UNKNOWN',stable_samples=count)
        return rows
