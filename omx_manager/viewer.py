"""Read-only LAN viewer. Reuse owner camera frames via bounded RAM files on demand."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs

from .config import ROOT, ConfigError, load
from .view_options import options, camera_name

LOG=logging.getLogger(__name__)
MAX_PACKET=2*1024*1024
ASSETS={'normal':'basket_view.png','low':'basket_low.png','compare':'basket_compare.png','pose':'basket_pose.png','failure':'basket_failure.png'}

def camera_config(cfg,name):
    if name not in cfg['hardware']['cameras']:raise KeyError(name)
    return dict(cfg,view=dict(options(cfg),camera=name))


class Publishers:
    def __init__(self,items):self.items=items
    def close(self):
        for item in self.items:
            try:item.close()
            except Exception:LOG.exception('[VIEW] 카메라 미리보기 정리 실패')


def channel(cfg):
    """Private tmpfs, separated by UID/device/camera identity; never SD image writes."""
    parent=Path('/dev/shm')
    if not parent.is_dir():raise ConfigError('Camera viewer requires Linux /dev/shm')
    path=parent/f'omx-view-{os.getuid()}-{cfg["device_id"]}'
    path.mkdir(mode=0o700,exist_ok=True)
    info=path.lstat()
    if path.is_symlink() or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise ConfigError('Viewer RAM directory must be owned by this user with mode 700')
    identity=json.dumps([camera_name(cfg),cfg['hardware']['cameras'][camera_name(cfg)]],sort_keys=True).encode()
    return path/hashlib.sha256(identity).hexdigest()[:20]


def write_bytes(path,data):
    path=Path(path);temporary=path.with_name(path.name+f'.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        with temporary.open('wb') as stream:stream.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_packet(path,max_age):
    with Path(path).open('rb') as stream:data=stream.read(MAX_PACKET+1)
    if not 8<=len(data)<=MAX_PACKET:raise ValueError('Invalid viewer packet length')
    size=struct.unpack('!I',data[:4])[0]
    if not 1<=size<=4096 or size+6>len(data):raise ValueError('Invalid viewer metadata')
    row=json.loads(data[4:4+size]);jpeg=data[4+size:]
    age=time.monotonic()-row['monotonic']
    if not 0<=age<=max_age or not jpeg.startswith(b'\xff\xd8'):raise ValueError('No fresh camera frame')
    row['frame_age_ms']=round(age*1000,1)
    return row,jpeg


class Publisher:
    """Background JPEG work only for an actively requested browser frame."""
    def __init__(self,cfg,camera):
        self.cfg=cfg;self.camera=camera;self.spec=options(cfg);self.path=channel(cfg)
        self.stop_event=threading.Event();self.thread=None;self.last=0.;self.stamp=None
        self.lease=None

    def step(self):
        now=time.monotonic()
        try:
            request=json.loads(self.path.with_suffix('.request').read_text())
            if not 0<=now-request['monotonic']<=3:return False
        except (OSError,ValueError,KeyError,TypeError):return False
        if now-self.last<1/self.spec['fps']:return False
        self.last=now
        from .basket import read_camera
        low_before=bool(getattr(self.camera,'_omx_exposure_blocked',False))
        frame,stamp=read_camera(self.camera,min(.5,self.spec['max_age_s']))
        if stamp==self.stamp:return False
        import cv2
        height,width=frame.shape[:2]
        if width>self.spec['max_width']:
            frame=cv2.resize(frame,(self.spec['max_width'],max(1,round(height*self.spec['max_width']/width))),interpolation=cv2.INTER_AREA)
        ok,encoded=cv2.imencode('.jpg',cv2.cvtColor(frame,cv2.COLOR_RGB2BGR),[cv2.IMWRITE_JPEG_QUALITY,self.spec['jpeg_quality']])
        if not ok:raise RuntimeError('Cannot encode viewer frame')
        row=dict(device=self.cfg['device_id'],camera=camera_name(self.cfg),monotonic=stamp,at=time.time(),pid=os.getpid(),
                 camera_state='INSPECTION_LOW_OR_RESTORING' if low_before or getattr(self.camera,'_omx_exposure_blocked',False) else 'NORMAL',
                 width=frame.shape[1],height=frame.shape[0])
        header=json.dumps(row,allow_nan=False).encode();packet=struct.pack('!I',len(header))+header+encoded.tobytes()
        if len(packet)>MAX_PACKET:raise RuntimeError('Viewer frame too large')
        write_bytes(self.path.with_suffix('.frame'),packet);self.stamp=stamp
        return True

    def _run(self):
        next_warning=0.
        while not self.stop_event.wait(.2):
            try:self.step()
            except Exception as exc:
                if time.monotonic()>=next_warning:
                    LOG.warning('[VIEW] Preview unavailable (robot unchanged): %s',exc);next_warning=time.monotonic()+30

    def start(self):
        from .hardware import Lock
        self.lease=Lock(self.path.with_suffix('.producer.lock'));self.lease.__enter__()
        self.thread=threading.Thread(target=self._run,name='omx-view-jpeg',daemon=True)
        self.thread.start();return self

    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=1)
        if self.thread and self.thread.is_alive():return # keep lease until process ends
        if self.lease:self.lease.__exit__(None,None,None);self.lease=None


def attach(cfg,camera):
    """Optional viewer failure must never fail a working robot/camera."""
    try:return Publisher(cfg,camera).start()
    except Exception as exc:LOG.warning('[VIEW] Preview disabled: %s',exc);return None


def attach_robot(robot):
    try:
        from .hooks import managed_config
        cfg=managed_config()
        if cfg is None:return
        items=[p for name,camera in robot.cameras.items() if name in cfg['hardware']['cameras']
               if (p:=attach(camera_config(cfg,name),camera)) is not None]
        robot._omx_view=Publishers(items) if items else None
    except Exception as exc:LOG.warning("[VIEW] Preview disabled: %s",exc)


PAGE=r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OMX 상단·손목 카메라</title><style>body{font:16px system-ui;background:#101820;color:#e4edf4;margin:16px}button,a{font:inherit;margin:4px;padding:8px}a{color:#8dd5ff}small{color:#a9bdcb}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.card{background:#192733;border:1px solid #415b70;border-radius:10px;padding:12px}img{display:block;width:100%;background:#263746;aspect-ratio:4/3;object-fit:contain}#saved{max-width:900px}#state{white-space:pre-wrap}.status{min-height:42px;color:#a9bdcb}h2,h3{margin:8px 0}@media(max-width:700px){.grid{grid-template-columns:1fr}}</style>
<h2 id="title">OMX 카메라</h2><nav><a href="/all" target="_top">로봇별 탭 / 2대 보기</a><button id="toggle">실시간 일시정지</button></nav>
<p><small>기존 카메라 영상 공유 · 기본 1초 간격 · 저노출 검사 중에는 해당 영상 상태 표시 · 보기 전용</small></p>
<div id="state">연결 중…</div><div id="cameras" class="grid"></div>
<details><summary>저장된 상단 카메라 스냅샷</summary><p>저장 시점의 사진입니다. 촬영은 기존 터미널 명령으로 수행합니다.</p>
<button onclick="showSaved('normal')">원본 + ROI/격자</button><button onclick="showSaved('low')">저노출 스냅샷</button><button onclick="showSaved('compare')">기준·현재·차이</button><button onclick="showSaved('pose')">바구니 추적 영역</button><button onclick="showSaved('failure')">마지막 검사 실패</button>
<div id="savedTime"></div><a id="savedLink" download><img id="saved" alt="스냅샷 선택"></a></details>
<script>
let paused=false,busy=false,assets={};const cards=new Map(),state=document.querySelector('#state');
function clearImage(img){if(img.dataset.url){URL.revokeObjectURL(img.dataset.url);delete img.dataset.url;}img.removeAttribute('src');}
function card(key,label){if(cards.has(key))return cards.get(key);const panel=document.createElement('section');panel.className='card';const title=document.createElement('h3');title.textContent=label+' 카메라 ('+key+')';const status=document.createElement('div');status.className='status';const img=document.createElement('img');img.alt=label+' 최신 영상';const save=document.createElement('a');save.textContent='이 카메라 영상 저장';save.download=key+'.jpg';save.href='/live.jpg?camera='+encodeURIComponent(key);panel.append(title,status,img,save);document.querySelector('#cameras').append(panel);const c={img,status};cards.set(key,c);return c;}
document.querySelector('#toggle').onclick=()=>{paused=!paused;document.querySelector('#toggle').textContent=paused?'실시간 재개':'실시간 일시정지';if(paused){for(const c of cards.values())clearImage(c.img);state.textContent='보기 일시정지';}else tick();};
function showSaved(kind){const a=assets[kind];document.querySelector('#savedTime').textContent=a?a.name+' · 저장: '+new Date(a.at*1000).toLocaleString():'저장된 사진이 없습니다';if(!a){document.querySelector('#saved').removeAttribute('src');return;}const url='/saved/'+kind+'.png?t='+Date.now();document.querySelector('#saved').src=url;document.querySelector('#savedLink').href=url;}
async function tick(){if(paused||document.hidden||busy)return;busy=true;try{const r=await fetch('/status.json?camera=all&t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const s=await r.json();if(paused||document.hidden)return;assets=s.assets;document.querySelector('#title').textContent=s.device.toUpperCase()+' · 상단 / 손목';state.textContent='카메라별 수신 상태';await Promise.all(Object.entries(s.cameras).map(async([key,v])=>{const c=card(key,v.label);if(!v.live){clearImage(c.img);c.status.textContent='영상 대기 · 실행기 또는 카메라 단독 뷰어를 확인하세요';return;}try{const r=await fetch('/live.jpg?camera='+encodeURIComponent(key)+'&t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const blob=await r.blob();if(paused||document.hidden)return;clearImage(c.img);const url=URL.createObjectURL(blob);c.img.src=url;c.img.dataset.url=url;c.status.textContent=(v.frame.camera_state==='NORMAL'?'원본 영상':'검사 저노출 / 복원 중')+' · 영상 나이 '+v.frame.frame_age_ms+'ms';}catch(e){clearImage(c.img);c.status.textContent='이 카메라 영상 갱신 대기';}}));}catch(e){for(const c of cards.values())clearImage(c.img);state.textContent='연결 끊김: '+e.message;}finally{busy=false;}}
document.addEventListener('visibilitychange',()=>{if(document.hidden){for(const c of cards.values())clearImage(c.img);}else tick();});window.addEventListener('pagehide',()=>{for(const c of cards.values())clearImage(c.img);});setInterval(tick,1000);tick();
</script></html>'''

ALL=r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OMX 카메라 모니터</title><style>body{font:16px system-ui;background:#101820;color:#e4edf4;margin:16px}h2{margin:8px 0}button,input{font:inherit;padding:10px;margin:4px;border-radius:6px}button{background:#263746;color:white;border:1px solid #60798a;cursor:pointer}button[aria-selected=true]{background:#1b637e;border-color:#8dd5ff}.grid{display:grid;grid-template-columns:1fr;gap:12px}.grid.pair{grid-template-columns:repeat(2,minmax(0,1fr))}iframe{width:100%;height:920px;border:1px solid #415b70;border-radius:10px;background:#192733;box-sizing:border-box}input{width:160px}small{color:#a9bdcb}@media(max-width:1100px){.grid.pair{grid-template-columns:1fr}}</style><h2>OMX 카메라 모니터</h2><p><small>로봇을 선택하면 상단·손목 영상을 함께 표시합니다. 선택하지 않은 로봇의 영상 요청은 중지합니다.</small></p><nav id="tabs" role="tablist" aria-label="로봇 선택"></nav><details><summary>Pi 연결 주소 설정</summary><div id="fields"></div><button id="apply">주소 적용</button><p>각 Pi에서 omx_view를 실행하세요. 기본 포트는 8080입니다.</p></details><div class="grid" id="grid"></div><script>
const fields=document.querySelector('#fields'),grid=document.querySelector('#grid'),tabs=document.querySelector('#tabs');const port=location.port||'8080';let selected=[1];for(let n=1;n<=4;n++){const label=document.createElement('label');label.textContent='OMX'+n;const input=document.createElement('input');input.id='ip'+n;input.value='192.168.0.'+(150+n);input.setAttribute('aria-label','OMX'+n+' IP');label.append(input);fields.append(label);}
function render(){grid.replaceChildren();grid.classList.toggle('pair',selected.length===2);for(const button of tabs.children)button.setAttribute('aria-selected',String(button.dataset.ids===selected.join(',')));for(const n of selected){const ip=document.querySelector('#ip'+n).value.trim();if(!/^(\d{1,3}\.){3}\d{1,3}$/.test(ip)||ip.split('.').some(v=>+v>255)){const error=document.createElement('p');error.textContent='OMX'+n+' IP 주소를 확인하세요';grid.append(error);continue;}const frame=document.createElement('iframe');frame.src='http://'+ip+':'+port+'/';frame.title='OMX'+n+' 상단·손목 카메라';grid.append(frame);}}
for(const [label,ids] of [['OMX1',[1]],['OMX2',[2]],['OMX3',[3]],['OMX4',[4]],['OMX1 + 2',[1,2]],['OMX3 + 4',[3,4]]]){const button=document.createElement('button');button.textContent=label;button.role='tab';button.dataset.ids=ids.join(',');button.onclick=()=>{selected=ids;render();};tabs.append(button);}document.querySelector('#apply').onclick=render;render();
</script></html>'''

class ViewerServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,cfg,address=None):
        self.cfg=cfg;self.spec=options(cfg);self.path=channel(cfg)
        self.paths={name:channel(camera_config(cfg,name)) for name in cfg['hardware']['cameras']}
        self.asset_dir=ROOT/'state'/'manager'/cfg['device_id']
        self.token=f'{os.getpid()}-{time.time_ns()}'
        self.request_lock=threading.Lock();self.next_request={}
        super().__init__(address or (self.spec['bind'],self.spec['port']),ViewerHandler)

    def touch(self,name=None):
        name=name or camera_name(self.cfg);path=self.paths[name]
        with self.request_lock:
            if time.monotonic()<self.next_request.get(name,0):return
            write_bytes(path.with_suffix('.request'),json.dumps(dict(monotonic=time.monotonic(),token=self.token)).encode())
            self.next_request[name]=time.monotonic()+.5

    def frame(self,name=None):
        name=name or camera_name(self.cfg)
        row,jpeg=read_packet(self.paths[name].with_suffix('.frame'),self.spec['max_age_s'])
        if row['device']!=self.cfg['device_id'] or row['camera']!=name:raise ValueError('Camera identity mismatch')
        return row,jpeg

    def asset(self,kind):
        if kind not in ASSETS:raise FileNotFoundError()
        path=self.asset_dir/ASSETS[kind]
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_PACKET:raise FileNotFoundError()
        return path

    def status(self,name=None):
        name=name or camera_name(self.cfg)
        result=dict(device=self.cfg['device_id'],camera=name,live=False,assets={},cameras={},
                    message='최신 영상 없음. 모델/보정/바구니 watch 실행 중이면 자동 연결됩니다. 정지 상태의 영상은 omx_view --camera-only 사용.')
        for kind in ASSETS:
            try:
                path=self.asset(kind);result['assets'][kind]=dict(name=path.name,at=path.stat().st_mtime)
            except OSError:pass
        for key in self.paths:
            row=dict(camera=key,label='손목' if key=='wrist' else '상단' if key in ('top','front') else key,live=False)
            try:row.update(live=True,frame=self.frame(key)[0])
            except (OSError,ValueError,KeyError,TypeError):pass
            result['cameras'][key]=row
        if result['cameras'][name]['live']:result.update(live=True,frame=result['cameras'][name]['frame'])
        return result

    def server_close(self):
        for source in self.paths.values():
            try:
                path=source.with_suffix('.request')
                if json.loads(path.read_text()).get('token')==self.token:path.unlink(missing_ok=True)
            except (OSError,ValueError):pass
        super().server_close()


class ViewerHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(3)
    def log_message(self,*args):pass
    def reply(self,code,mime,data):
        self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        # Only the configured laptop and local checks. No folder listing or arbitrary files.
        if self.client_address[0] not in ('127.0.0.1',self.server.spec['allow_ip']):
            self.reply(403,'text/plain; charset=utf-8','허용된 노트북 IP가 아닙니다. view.allow_ip 확인'.encode());return
        path=urlsplit(self.path).path
        try:
            name=parse_qs(urlsplit(self.path).query).get('camera',[None])[0]
            if name not in (None,'all') and name not in self.server.paths:
                self.reply(404,'text/plain; charset=utf-8','등록되지 않은 카메라입니다'.encode());return
            if path in ('/','/all'):
                self.reply(200,'text/html; charset=utf-8',(PAGE if path=='/' else ALL).encode())
            elif path=='/status.json':
                for key in (self.server.paths if name=='all' else [name]):self.server.touch(key)
                self.reply(200,'application/json',json.dumps(self.server.status(None if name=='all' else name),ensure_ascii=False).encode())
            elif path=='/live.jpg':
                self.server.touch(name);row,jpeg=self.server.frame(name);self.reply(200,'image/jpeg',jpeg)
            elif path in ('/saved/normal.png','/saved/low.png','/saved/compare.png','/saved/pose.png','/saved/failure.png'):
                kind=path.split('/')[-1].split('.')[0];self.reply(200,'image/png',self.server.asset(kind).read_bytes())
            else:self.reply(404,'text/plain',b'Not found')
        except (OSError,ValueError,KeyError,TypeError):
            self.reply(503 if path=='/live.jpg' else 404,'text/plain',b'No fresh frame or saved image')


@contextlib.contextmanager
def standalone_camera(cfg):
    from .runtime import device_lock
    from .exposure import require_restored
    require_restored(cfg)
    with device_lock(cfg):
        # Camera only: never instantiate a follower, motor bus, policy or MQTT client.
        import draccus
        from lerobot.cameras import CameraConfig,make_cameras_from_configs
        from lerobot.cameras.opencv import OpenCVCameraConfig
        from lerobot.cameras.realsense import RealSenseCameraConfig
        configs={name:draccus.decode(CameraConfig,spec) for name,spec in cfg['hardware']['cameras'].items()}
        cameras=make_cameras_from_configs(configs);publishers=[]
        try:
            for name,camera in cameras.items():
                camera.connect();publishers.append(Publisher(camera_config(cfg,name),camera).start())
            yield cameras
        finally:
            Publishers(publishers).close()
            for camera in cameras.values():
                try:
                    if camera.is_connected:camera.disconnect()
                except Exception:LOG.exception('[VIEW] 카메라 단독 보기 정리 실패')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--camera-only',action='store_true',help='모델 정지 상태에서 상단·손목 RGB 카메라만 열기, 팔 이동 없음')
    args=parser.parse_args(argv)
    try:
        cfg=load(args.config)
        server=ViewerServer(cfg)
        try:
            print(f"[VIEW] {cfg['device_id']} / {', '.join(cfg['hardware']['cameras'])} / port={server.server_port} / laptop={options(cfg)['allow_ip']}",flush=True)
            print('[VIEW] 노트북 브라우저: http://이_Pi의_IP:'+str(server.server_port)+'/ (4대 보기: /all)',flush=True)
            print('[VIEW] 카메라 단독 점유 모드' if args.camera_only else '[VIEW] 공유 프레임 보기: 카메라를 새로 열지 않습니다.',flush=True)
            with standalone_camera(cfg) if args.camera_only else contextlib.nullcontext():
                server.serve_forever(poll_interval=.25)
        finally:server.server_close()
    except KeyboardInterrupt:print('[VIEW] 보기 종료. 모터·모델 명령 없음.');return 130
    except Exception as exc:print(f'[VIEW] {type(exc).__name__}: {exc}');return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
