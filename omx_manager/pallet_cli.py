"""OMX1/2 pallet setup and shared-camera capture; no model or motor actions."""
import argparse,json,os,time,uuid
from pathlib import Path
from .config import load,save,write_json
from .runtime import device_lock
from .hardware import Lock
from . import pallet as P


def request(cfg,action,item=None,*,timeout=12):
    directory=P.directory(cfg)
    with Lock(directory/'pallet_cli.lock'):
        current=json.loads((directory/'pallet_stock.json').read_text())
        if not 0<=time.time()-current['at']<=P.options(cfg)['interval_s']*2.5:raise RuntimeError('팔레트 검사 실행기가 없습니다. 설정을 켜고 로봇 서비스를 시작하세요')
        token=uuid.uuid4().hex
        write_json(directory/'pallet_request.json',dict(token=token,session=current['session'],at=time.time(),action=action,item=item,confirm_empty=action=='capture'))
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                reply=json.loads((directory/'pallet_reply.json').read_text())
                if reply.get('token')==token:
                    if not reply['ok']:raise RuntimeError(reply['message'])
                    return reply
            except (OSError,ValueError):pass
            time.sleep(.1)
        raise TimeoutError('팔레트 촬영 응답 시간 초과. 로봇 서비스·홈 대기 상태를 확인하세요')


def invoke(path,action,item=None,value=None,confirm_empty=False,json_output=False):
    cfg=load(path)
    if cfg['device_id'] not in P.ITEMS:raise ValueError('팔레트 재고 검사는 OMX1·2 전용입니다')
    if item is not None and item not in P.ITEMS[cfg['device_id']]:raise ValueError('이 장비에 없는 외부 품목입니다')
    if action in ('setup','on','off','roi'):
        with device_lock(cfg):
            spec=P.options(cfg);spec['items']=dict(spec['items'])
            for key in P.ITEMS[cfg['device_id']]:spec['items'].setdefault(key,dict(roi=None))
            if action in ('on','off'):spec['enabled']=action=='on'
            if action=='roi':
                if not item:raise ValueError('ROI를 지정할 외부 품목이 필요합니다')
                spec['items'][item]=dict(roi=json.loads(value))
            cfg['pallet_stock']=spec;save(path,cfg)
        print('팔레트 설정 저장 완료. 로봇 서비스 재시작 후 적용됩니다');return
    if action=='capture' and not confirm_empty:raise ValueError('해당 팔레트를 완전히 비운 뒤 --confirm-empty로 확인하세요')
    if action in ('snapshot','capture'):
        if action=='capture' and item is None:raise ValueError('빈 기준을 저장할 품목이 필요합니다')
        print(json.dumps(request(cfg,action,item),ensure_ascii=False,indent=2));return
    if action=='status':
        from .pallet_runtime import mqtt_payload
        data={}
        try:data=json.loads((P.directory(cfg)/'pallet_stock.json').read_text())
        except (OSError,ValueError):pass
        # A live heartbeat must belong to the same producer, not an old session.
        try:
            heartbeat=json.loads((P.directory(cfg)/'heartbeat.json').read_text())
            alive=heartbeat['pid']==data.get('pid') and 0<=time.monotonic()-heartbeat['monotonic']<=cfg['runtime']['heartbeat_timeout_s']
        except (OSError,ValueError,KeyError):alive=False
        result=mqtt_payload(cfg,P.directory(cfg)/'pallet_stock.json',data.get('session'),data.get('pid'),idle=alive)
        if json_output:print(json.dumps(result,ensure_ascii=False,indent=2))
        else:
            names={'PRESENT':'물건 있음','EMPTY':'비었음','UNKNOWN':'판정 불가'}
            parts=[]
            for k,v in result['stock'].items():
                detail=result.get('items',{}).get(k,{}) if isinstance(result.get('items',{}),dict) else {}
                reason=detail.get('reason') if isinstance(detail,dict) else None
                parts.append(k+': '+names[v]+(' ('+str(reason)+')' if v=='UNKNOWN' and reason else ''))
            print('[STOCK] '+cfg['device_id']+' | '+' | '.join(parts)+' | '+result['reason'])
        return result
    raise ValueError('지원하지 않는 팔레트 명령')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('action',choices=('setup','on','off','roi','snapshot','capture','status'));parser.add_argument('--item');parser.add_argument('--value');parser.add_argument('--confirm-empty',action='store_true')
    parser.add_argument('--json',action='store_true',dest='json_output')
    args=parser.parse_args(argv)
    try:invoke(args.config,args.action,args.item,args.value,args.confirm_empty,args.json_output)
    except (Exception,KeyboardInterrupt) as exc:print('[STOCK] 팔레트 명령 실패:',str(exc));return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
