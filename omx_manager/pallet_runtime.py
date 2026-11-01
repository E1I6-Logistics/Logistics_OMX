"""Low-rate pallet worker. No serial reads, new camera opens, or policy calls."""
import json,logging,os,threading,time
from pathlib import Path
from . import pallet as P
from .config import JOINTS,write_json
from .home import arrival_tolerances

LOG=logging.getLogger(__name__)


def process_start(pid):
    if type(pid) is not int or pid<=0:return None
    try:
        stat=Path(f'/proc/{pid}/stat').read_text();fields=stat[stat.rfind(')')+2:].split()
        return None if fields[0]=='Z' else int(fields[19])
    except (OSError,ValueError,IndexError):return None


class Worker:
    def __init__(self,robot,cfg):
        self.robot=robot;self.cfg=cfg;self.spec=P.options(cfg);self.inspector=P.Inspector(cfg)
        self.directory=P.directory(cfg);self.stop=threading.Event();self.pose=None;self.previous=None
        self.session=os.environ.get('OMX_SESSION');self.thread=None;self.process_start_ticks=process_start(os.getpid())
        self.order_epoch=None
    def observe(self,obs):
        # Existing motor observation only. No hardware polling in this thread.
        self.pose=(time.monotonic(),{j:float(obs[j+'.pos']) for j in JOINTS})
    def gate(self):
        if self.stop.is_set() or not getattr(self.robot,'_omx_runtime_ready',False):return '모델 준비 중 또는 종료 중'
        try:
            data=json.loads((self.directory/'inventory.json').read_text())
            if type(data.get('remaining')) is not int or data['remaining']!=0:return '주문 수행 중 또는 주문 상태 미확인'
            self.order_epoch=data.get('order_started_at_epoch')
            stamp,pose=self.pose
            if not 0<=time.monotonic()-stamp<=1:return '관절 관측 지연'
            arrival=arrival_tolerances(self.cfg)
            if any(abs(pose[j]-self.cfg['home']['target'][j])>arrival[j] for j in JOINTS):return '홈 대기 자세 아님'
            if self.previous is None or any(abs(pose[j]-self.previous[j])>self.cfg['home']['still_delta'] for j in JOINTS):
                self.previous=pose;return '팔 정지 확인 중'
            self.previous=pose
        except (OSError,ValueError,TypeError,KeyError):return '대기 상태 확인 불가'
        return None
    def frame(self):
        from .basket import read_camera
        camera=self.robot.cameras[self.spec['camera']]
        if getattr(camera,'_omx_exposure_blocked',False):raise RuntimeError('카메라 검사 노출 또는 복원 중')
        return read_camera(camera,self.spec['max_frame_age_s'])
    def save(self,rows):
        data=dict(schema_version=1,robot_id=self.cfg['device_id'],session=self.session,pid=os.getpid(),process_start_ticks=self.process_start_ticks,at=time.time(),
                  monotonic=time.monotonic(),valid_for_s=self.spec['interval_s']*2.5,
                  stock={k:v['state'] for k,v in rows.items()},items=rows,exact_item_count=None,order_epoch=self.order_epoch)
        write_json(self.directory/'pallet_stock.json',data,durable=False)
        return data
    def check(self):
        reason=self.gate()
        if reason:return self.save(self.inspector.unknown(reason))
        epoch=self.order_epoch
        frame,stamp=self.frame();rows=self.inspector.update(frame,stamp,time.monotonic())
        reason=self.gate()
        if self.order_epoch!=epoch:reason='검사 도중 주문 상태 변경'
        return self.save(self.inspector.unknown(reason) if reason else rows)
    def command(self):
        path=self.directory/'pallet_request.json'
        if not path.exists():return
        request=json.loads(path.read_text());path.unlink(missing_ok=True)
        token=request.get('token');result=dict(device=self.cfg['device_id'],token=token,session=self.session,ok=False,at=time.time())
        try:
            if (request.get('session')!=self.session or not 0<=time.time()-request['at']<=15
                    or not isinstance(token,str) or len(token)!=32):raise RuntimeError('이전 실행 또는 만료된 촬영 요청')
            reason=self.gate()
            if reason:raise RuntimeError(reason)
            action=request['action'];item=request.get('item');epoch=self.order_epoch
            if action=='snapshot':
                import cv2
                frame,_=self.frame();image=cv2.cvtColor(frame,cv2.COLOR_RGB2BGR)
                for x in range(0,image.shape[1],80):cv2.line(image,(x,0),(x,image.shape[0]-1),(0,180,0),1);cv2.putText(image,str(x),(x+2,14),0,.4,(0,255,0),1)
                for y in range(0,image.shape[0],80):cv2.line(image,(0,y),(image.shape[1]-1,y),(0,180,0),1);cv2.putText(image,str(y),(2,y+14),0,.4,(0,255,0),1)
                for key,row in self.spec['items'].items():
                    if row.get('roi'):
                        x,y,w,h=row['roi'];cv2.rectangle(image,(x,y),(x+w,y+h),(0,220,255),2);cv2.putText(image,key,(x+4,y+20),0,.6,(0,0,255),2)
                from .viewer import label_image
                image=label_image(image,self.cfg['device_id'],self.spec['camera'])
                destination=self.directory/(self.cfg['device_id']+'_pallet_view.png')
                if not cv2.imwrite(str(destination),image):raise OSError('팔레트 좌표 사진 저장 실패')
            elif action=='diagnose' and item in P.ITEMS[self.cfg['device_id']]:
                import cv2,numpy as np
                frame,stamp=self.frame()
                current=P.crop(frame,self.spec['items'].get(item,{}).get('roi'))
                reference=P.load_reference(self.cfg,item)
                started=time.perf_counter()
                item_spec=P.item_options(self.spec,item)
                comparison=P.compare(current,reference,item_spec)
                comparison['processing_ms']=(time.perf_counter()-started)*1000
                if self.gate() or self.order_epoch!=epoch:raise RuntimeError('진단 도중 주문·팔 상태 변경')
                self.directory.mkdir(parents=True,exist_ok=True)
                prefix=self.cfg['device_id']+'_pallet_'+item
                destination=self.directory/(prefix+'_diagnose.png')
                gap=np.abs(current.astype(float)-reference.astype(float)).max(axis=2)
                difference=cv2.cvtColor(np.uint8(np.clip(gap*3,0,255)),cv2.COLOR_GRAY2RGB)
                panels=[]
                for title,image in (('EMPTY REFERENCE',reference),('CURRENT',current),('RAW DIFFERENCE',difference)):
                    panel=cv2.copyMakeBorder(cv2.cvtColor(image,cv2.COLOR_RGB2BGR),22,0,0,0,cv2.BORDER_CONSTANT)
                    cv2.putText(panel,title,(3,14),0,.32,(255,255,255),1);panels.append(panel)
                from .viewer import label_image
                image=label_image(np.hstack(panels),self.cfg['device_id'],'pallet '+item)
                if not cv2.imwrite(str(destination),image):raise OSError('팔레트 진단 사진 저장 실패')
                evidence=self.directory/(prefix+'_evidence.npz')
                np.savez_compressed(evidence,current=current,reference=reference,options=np.array(json.dumps(item_spec)),
                                    robot_id=np.array(self.cfg['device_id']),item=np.array(item))
                write_json(self.directory/(prefix+'_diagnose.json'),dict(comparison,at=time.time(),frame_stamp=stamp),durable=False)
                from .pallet_tuning import settings_hash
                result.update(comparison=comparison,evidence_path=str(evidence),frame_stamp=stamp,
                              binding=P.signature(self.cfg,item),settings_hash=settings_hash(self.spec))
            elif action=='tune' and item in P.ITEMS[self.cfg['device_id']]:
                from .pallet_tuning import apply_parameters
                destination=apply_parameters(self,item,request.get('payload'))
                result.update(message='품목별 설정 저장·즉시 적용 완료')
            elif action=='capture' and request.get('confirm_empty') is True and item in P.ITEMS[self.cfg['device_id']]:
                frames=[];stamps=[]
                for _ in range(3):
                    reason=self.gate()
                    if reason:raise RuntimeError(reason)
                    frame,stamp=self.frame()
                    if stamps and stamp<=stamps[-1]:raise RuntimeError('새 카메라 영상 대기 후 다시 촬영하세요')
                    frames.append(frame);stamps.append(stamp)
                    if self.stop.wait(.15):raise RuntimeError('촬영 중 실행기 종료')
                if self.gate() or self.order_epoch!=epoch or time.time()-request['at']>10:raise RuntimeError('촬영 도중 주문·팔 상태 변경 또는 요청 만료')
                destination=P.save_reference(self.cfg,item,frames)
                self.inspector=P.Inspector(self.cfg)
            else:raise RuntimeError('잘못된 촬영 요청 또는 빈 상태 확인 누락')
            result.update(ok=True,path=str(destination))
            result.setdefault('message','촬영·저장 완료')
        except Exception as exc:result['message']=str(exc)
        write_json(self.directory/'pallet_reply.json',result,durable=False)
    def run(self):
        next_check=0.
        while not self.stop.wait(.5):
            try:
                self.command()
                if time.monotonic()>=next_check:
                    next_check=time.monotonic()+self.spec['interval_s'];self.check()
            except Exception as exc:
                LOG.warning('[STOCK] 팔레트 검사 보류: %s',exc)
                try:self.save(self.inspector.unknown('검사 오류: '+str(exc)))
                except OSError:pass
    def start(self):
        self.save(self.inspector.unknown('검사 준비 중'))
        self.thread=threading.Thread(target=self.run,name='omx-pallet-stock',daemon=True);self.thread.start();return self
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
        if self.thread and self.thread.is_alive():raise RuntimeError('팔레트 검사 정리 대기 중')
        self.save(self.inspector.unknown('실행기 종료'))


def attach(robot,cfg):
    if cfg['device_id'] not in P.ITEMS or not P.options(cfg)['enabled']:return
    try:robot._omx_stock=Worker(robot,cfg).start()
    except Exception:LOG.exception('[STOCK] 팔레트 검사 시작 실패. 로봇 제어는 유지합니다')


def mqtt_payload(cfg,path,session,rollout_pid,*,now=None,idle=True):
    now=time.time() if now is None else now;s=P.options(cfg)
    result=dict(schema_version=1,robot_id=cfg['device_id'],stock={k:'UNKNOWN' for k in P.ITEMS.get(cfg['device_id'],())},at=now,
                reason='검사 비활성·이전 실행·오래된 결과',exact_item_count=None,valid_for_s=s['interval_s']*2.5)
    try:
        row=json.loads(path.read_text());inventory=json.loads(path.with_name('inventory.json').read_text())
        identity=process_start(rollout_pid)
        if (identity is not None and row.get('process_start_ticks')==identity and idle and inventory.get('remaining')==0 and row.get('order_epoch')==inventory.get('order_started_at_epoch')
                and s['enabled'] and row['robot_id']==cfg['device_id'] and row['session']==session and row['pid']==rollout_pid
                and 0<=now-row['at']<=result['valid_for_s'] and set(row['stock'])==set(result['stock'])
                and all(v in ('PRESENT','EMPTY','UNKNOWN') for v in row['stock'].values())):
            return dict(result,stock=row['stock'],observed_at=row['at'],reason='팔레트 관측 결과',items=row['items'])
    except (OSError,ValueError,TypeError,KeyError):pass
    return result
