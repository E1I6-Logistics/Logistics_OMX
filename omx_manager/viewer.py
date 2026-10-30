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
from urllib.parse import urlsplit

from .config import ROOT, ConfigError, load
from .view_options import options, camera_name

LOG=logging.getLogger(__name__)
MAX_PACKET=2*1024*1024
ASSETS={'normal':'basket_view.png','low':'basket_low.png'}


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
        name=camera_name(cfg)
        robot._omx_view=attach(cfg,robot.cameras[name])
    except Exception as exc:LOG.warning("[VIEW] Preview disabled: %s",exc)


PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OMX 카메라</title><style>body{font:16px system-ui;background:#101820;color:#e4edf4;margin:20px}button,a,input{font:inherit;margin:4px;padding:8px}a{color:#8dd5ff}img{display:block;max-width:100%;background:#263746;min-height:160px}#state{white-space:pre-wrap}small{color:#a9bdcb}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:10px}iframe{width:100%;height:610px;border:1px solid #415b70}label{display:inline-block}</style>
<h2 id="title">OMX 고정 카메라</h2><nav><a href="/all">4대 모아 보기</a><button id="toggle">실시간 일시정지</button><button onclick="location.reload()">새로고침</button></nav>
<p><small>모터·모델·노출을 조작하지 않는 보기 전용 화면. 영상은 보기용 JPEG이며 저노출 검사 중에는 해당 상태를 표시합니다.</small></p>
<div id="state">연결 중…</div><img id="live" alt="최신 카메라 영상 대기"><p><a id="download" href="/live.jpg" download="camera.jpg">현재 영상 저장</a></p>
<h3>저장된 스냅샷</h3><p>아래는 저장 시점의 사진입니다. 촬영은 기존 터미널 명령으로 수행합니다.</p>
<button onclick="showSaved('normal')">원본 + ROI/격자</button><button onclick="showSaved('low')">저노출 스냅샷</button>
<div id="savedTime"></div><a id="savedLink" download><img id="saved" alt="스냅샷 선택"></a>
<script>
let paused=false,busy=false,assets={};const state=document.querySelector('#state'),live=document.querySelector('#live');
document.querySelector('#toggle').onclick=()=>{paused=!paused;document.querySelector('#toggle').textContent=paused?'실시간 재개':'실시간 일시정지';if(paused){live.removeAttribute('src');state.textContent='보기 일시정지';}};
function showSaved(kind){const a=assets[kind];document.querySelector('#savedTime').textContent=a?a.name+' · 저장: '+new Date(a.at*1000).toLocaleString():'저장된 사진이 없습니다';if(!a){document.querySelector('#saved').removeAttribute('src');return;}let url='/saved/'+kind+'.png?t='+Date.now();document.querySelector('#saved').src=url;document.querySelector('#savedLink').href=url;}
async function tick(){if(paused||document.hidden||busy)return;busy=true;try{const r=await fetch('/status.json?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const s=await r.json();assets=s.assets;document.querySelector('#title').textContent=s.device+' · '+s.camera;state.textContent=s.live?'영상 수신 · '+s.frame.camera_state+' · 프레임 나이 '+s.frame.frame_age_ms+' ms':s.message;if(s.live){const r=await fetch('/live.jpg?t='+Date.now(),{cache:'no-store'});if(r.ok){const url=URL.createObjectURL(await r.blob());const old=live.dataset.url;live.src=url;live.dataset.url=url;if(old)URL.revokeObjectURL(old);}else{live.removeAttribute('src');state.textContent='영상 갱신 대기';}}else live.removeAttribute('src');}catch(e){live.removeAttribute('src');state.textContent='연결 끊김: '+e.message;}finally{busy=false;}}
setInterval(tick,1000);tick();
</script></html>'''

ALL='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OMX 4대 카메라</title><style>body{font:16px system-ui;background:#101820;color:white;margin:16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(450px,1fr));gap:10px}iframe{width:100%;height:850px;border:1px solid #415b70}input,button{font:inherit;padding:8px;margin:4px}</style><h2>OMX 4대 카메라</h2><p>각 Pi에서 omx_view를 실행하세요. 노트북에는 설치할 프로그램이 없습니다.</p><div id="fields"></div><button id="apply">연결</button><div class="grid" id="grid"></div><script>
const f=document.querySelector('#fields'),g=document.querySelector('#grid');for(let n=1;n<=4;n++){let i=document.createElement('input');i.id='ip'+n;i.value='192.168.0.'+(150+n);i.setAttribute('aria-label','OMX'+n+' IP');f.append(i);}
document.querySelector('#apply').onclick=()=>{g.replaceChildren();for(let n=1;n<=4;n++){let ip=document.querySelector('#ip'+n).value.trim();if(!/^(\\d{1,3}\\.){3}\\d{1,3}$/.test(ip)||ip.split('.').some(v=>+v>255))continue;let frame=document.createElement('iframe');frame.src='http://'+ip+':'+location.port+'/';frame.title='OMX'+n;g.append(frame);}};document.querySelector('#apply').click();
</script></html>'''


class ViewerServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,cfg,address=None):
        self.cfg=cfg;self.spec=options(cfg);self.path=channel(cfg)
        self.asset_dir=ROOT/'state'/'manager'/cfg['device_id']
        self.token=f'{os.getpid()}-{time.time_ns()}'
        self.request_lock=threading.Lock();self.next_request=0.
        super().__init__(address or (self.spec['bind'],self.spec['port']),ViewerHandler)

    def touch(self):
        with self.request_lock:
            if time.monotonic()<self.next_request:return
            write_bytes(self.path.with_suffix('.request'),json.dumps(dict(monotonic=time.monotonic(),token=self.token)).encode())
            self.next_request=time.monotonic()+.5

    def frame(self):
        row,jpeg=read_packet(self.path.with_suffix('.frame'),self.spec['max_age_s'])
        if row['device']!=self.cfg['device_id'] or row['camera']!=camera_name(self.cfg):raise ValueError('Camera identity mismatch')
        return row,jpeg

    def asset(self,kind):
        if kind not in ASSETS:raise FileNotFoundError()
        path=self.asset_dir/ASSETS[kind]
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_PACKET:raise FileNotFoundError()
        return path

    def status(self):
        result=dict(device=self.cfg['device_id'],camera=camera_name(self.cfg),live=False,assets={},
                    message='최신 영상 없음. 모델/보정/바구니 watch 실행 중이면 자동 연결됩니다. 정지 상태의 영상은 omx_view --camera-only 사용.')
        for kind in ASSETS:
            try:
                path=self.asset(kind);result['assets'][kind]=dict(name=path.name,at=path.stat().st_mtime)
            except OSError:pass
        try:result.update(live=True,frame=self.frame()[0])
        except (OSError,ValueError,KeyError,TypeError):pass
        return result

    def server_close(self):
        try:
            path=self.path.with_suffix('.request')
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
            if path in ('/','/all'):
                self.reply(200,'text/html; charset=utf-8',(PAGE if path=='/' else ALL).encode())
            elif path=='/status.json':
                self.server.touch();self.reply(200,'application/json',json.dumps(self.server.status(),ensure_ascii=False).encode())
            elif path=='/live.jpg':
                self.server.touch();row,jpeg=self.server.frame();self.reply(200,'image/jpeg',jpeg)
            elif path in ('/saved/normal.png','/saved/low.png'):
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
        name=camera_name(cfg);spec=draccus.decode(CameraConfig,cfg['hardware']['cameras'][name])
        camera=make_cameras_from_configs({name:spec})[name];publisher=None
        try:
            camera.connect();publisher=Publisher(cfg,camera).start();yield camera
        finally:
            if publisher:publisher.close()
            if camera.is_connected:camera.disconnect()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--camera-only',action='store_true',help='Model stopped: exclusively open fixed RGB camera only, no arm movement')
    args=parser.parse_args(argv)
    try:
        cfg=load(args.config)
        server=ViewerServer(cfg)
        try:
            print(f"[VIEW] {cfg['device_id']} / {camera_name(cfg)} / port={server.server_port} / laptop={options(cfg)['allow_ip']}",flush=True)
            print('[VIEW] 노트북 브라우저: http://이_Pi의_IP:'+str(server.server_port)+'/ (4대 보기: /all)',flush=True)
            print('[VIEW] 카메라 단독 점유 모드' if args.camera_only else '[VIEW] 공유 프레임 보기: 카메라를 새로 열지 않습니다.',flush=True)
            with standalone_camera(cfg) if args.camera_only else contextlib.nullcontext():
                server.serve_forever(poll_interval=.25)
        finally:server.server_close()
    except KeyboardInterrupt:print('[VIEW] 보기 종료. 모터·모델 명령 없음.');return 130
    except Exception as exc:print(f'[VIEW] {type(exc).__name__}: {exc}');return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
