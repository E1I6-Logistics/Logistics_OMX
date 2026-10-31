"""Viewer tests use synthetic RGB and localhost; no motors, cameras or broker."""
import copy
from http.client import HTTPConnection
import json
from pathlib import Path
from types import SimpleNamespace as NS
import threading
import time
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from omx_manager import config as C,viewer as V,view_options as O

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def rig(tmp_path,monkeypatch):
    cfg=C.load(ROOT/'params/omx4.yaml')
    cfg['view']['fps']=2.
    monkeypatch.setattr(V,'ROOT',tmp_path)
    monkeypatch.setattr(V,'channel',lambda c:tmp_path/c['device_id'])
    camera=NS(_omx_exposure_blocked=False)
    camera.reads=0
    frame=np.zeros((480,640,3),np.uint8);frame[:,:,0]=200
    def read(**kwargs):
        camera.reads+=1
        return frame.copy(),time.perf_counter()
    camera.read_latest_with_timestamp=read
    return cfg,camera,frame,tmp_path


def request(publisher,age=0):
    V.write_bytes(publisher.path.with_suffix('.request'),json.dumps(dict(monotonic=time.monotonic()-age)).encode())


@pytest.mark.parametrize('n,expected',[(1,'front'),(2,'front'),(3,'top'),(4,'top')])
def test_camera_mapping_and_original_config_preserved(n,expected):
    cfg=C.load(ROOT/f'params/omx{n}.yaml');original=copy.deepcopy(cfg)
    assert O.camera_name(cfg)==expected
    assert O.options(cfg)['allow_ip']=='192.168.0.19'
    assert cfg==original
    cfg.pop('view');C.validate(cfg) # deployed site YAML remains compatible


@pytest.mark.parametrize('key,value',[('allow_ip','0.0.0.0/0'),('port',80),('fps',30),('max_width',99999),('jpeg_quality',100),('camera','absent'),('max_age_s',float('nan'))])
def test_invalid_view_config_rejected(rig,key,value):
    cfg,*_=rig;cfg['view'][key]=value
    with pytest.raises(C.ConfigError):C.validate(cfg)


def test_idle_and_expired_requests_do_not_copy_or_encode_frames(rig,monkeypatch):
    cfg,cam,_,_=rig;p=V.Publisher(cfg,cam)
    monkeypatch.setattr(cv2,'imencode',Mock(side_effect=AssertionError('No idle JPEG work')))
    assert not p.step();request(p,age=4);assert not p.step()
    assert cam.reads==0


def test_requested_frame_has_rgb_color_fresh_timestamp_and_rate_limit(rig):
    cfg,cam,frame,_=rig;p=V.Publisher(cfg,cam);request(p)
    assert p.step();assert not p.step();assert cam.reads==1
    row,jpeg=V.read_packet(p.path.with_suffix('.frame'),2)
    decoded=cv2.imdecode(np.frombuffer(jpeg,np.uint8),cv2.IMREAD_COLOR)
    assert row['device']=='omx4' and row['camera']=='top' and row['camera_state']=='NORMAL'
    assert decoded[10,10,2]>190 and decoded[10,10,0]<10
    assert np.array_equal(frame[:,:,0],np.full((480,640),200,np.uint8))


def test_preview_resize_does_not_modify_model_frame(rig):
    cfg,cam,frame,_=rig;cfg['view']['max_width']=320
    p=V.Publisher(cfg,cam);request(p);assert p.step()
    row,jpeg=V.read_packet(p.path.with_suffix('.frame'),2)
    assert (row['width'],row['height'])==(320,240)
    assert frame.shape==(480,640,3)


def test_low_exposure_is_labelled_without_control_writes(rig):
    cfg,cam,_,_=rig;cam._omx_exposure_blocked=True
    p=V.Publisher(cfg,cam);request(p);assert p.step()
    assert V.read_packet(p.path.with_suffix('.frame'),2)[0]['camera_state']=='INSPECTION_LOW_OR_RESTORING'
    assert not hasattr(cam,'set')


def test_duplicate_source_or_stale_frame_never_looks_live(rig):
    cfg,cam,_,_=rig;p=V.Publisher(cfg,cam);request(p);assert p.step()
    path=p.path.with_suffix('.frame');data=path.read_bytes();size=V.struct.unpack('!I',data[:4])[0]
    row=json.loads(data[4:4+size]);row['monotonic']=time.monotonic()-3
    header=json.dumps(row).encode();path.write_bytes(V.struct.pack('!I',len(header))+header+data[4+size:])
    with pytest.raises(ValueError,match='fresh'):V.read_packet(path,2)
    cam.read_latest_with_timestamp=lambda **kw: (np.zeros((10,10,3),np.uint8),time.monotonic()-1)
    p.last=0
    with pytest.raises(TimeoutError):p.step()


def test_corrupt_oversized_packet_is_bounded(rig):
    _,_,_,tmp=rig;path=tmp/'bad.frame'
    for data in (b'bad',b'0'*(V.MAX_PACKET+1),V.struct.pack('!I',999999)+b'abcdef'):
        path.write_bytes(data)
        with pytest.raises(ValueError):V.read_packet(path,2)


def test_background_stops_and_duplicate_publisher_is_refused(rig):
    cfg,cam,_,_=rig;p=V.Publisher(cfg,cam).start()
    try:
        with pytest.raises(RuntimeError):V.Publisher(cfg,cam).start()
        assert cam.reads==0
    finally:p.close()
    assert not p.thread.is_alive()
    q=V.Publisher(cfg,cam).start();q.close()


@pytest.fixture
def http(rig):
    cfg,cam,_,tmp=rig
    server=V.ViewerServer(cfg,('127.0.0.1',0));thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
    def get(path,method='GET'):
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        try:
            conn.request(method,path);response=conn.getresponse();return response.status,dict(response.getheaders()),response.read()
        finally:conn.close()
    yield server,get,cam,tmp
    server.shutdown();server.server_close();thread.join(timeout=1)


def test_http_shared_mode_is_camera_free_and_whitelists_files(http):
    server,get,cam,tmp=http
    for url in ('/','/all'):
        code,headers,data=get(url);assert code==200 and 'text/html' in headers['Content-Type']
    code,headers,data=get('/status.json');status=json.loads(data)
    assert not status['live'] and status['camera']=='top' and status['assets']=={}
    assert cam.reads==0
    assert get('/live.jpg')[0]==503
    for path in ('/../params/omx4.yaml','/saved/../../config.yaml','/saved/other.png','/state/manager/omx4/jobs.json'):
        assert get(path)[0]==404
    assert get('/status.json','POST')[0]==501


def test_http_serves_fresh_shared_frame_and_marks_missing_source(http):
    server,get,cam,tmp=http
    get('/status.json');p=V.Publisher(server.cfg,cam);assert p.step()
    status=json.loads(get('/status.json')[2]);assert status['live']
    code,headers,data=get('/live.jpg');assert code==200 and data.startswith(b'\xff\xd8')
    assert headers['Cache-Control']=='no-store'
    p.path.with_suffix('.frame').unlink();assert not json.loads(get('/status.json')[2])['live']


def test_only_saved_image_names_served_and_symlinks_blocked(http):
    server,get,_,tmp=http
    server.asset_dir.mkdir(parents=True)
    path=server.asset_dir/'basket_low.png';path.write_bytes(b'\x89PNG\r\n\x1a\nexample')
    code,headers,data=get('/saved/low.png');assert code==200 and data==path.read_bytes()
    assert json.loads(get('/status.json')[2])['assets']['low']['name']=='basket_low.png'
    path.unlink();outside=tmp/'secret';outside.write_text('secret');path.symlink_to(outside)
    assert get('/saved/low.png')[0]==404


def test_foreign_laptop_is_denied_before_demand_write(rig):
    cfg,_,_,tmp=rig
    h=object.__new__(V.ViewerHandler);h.client_address=('192.168.0.22',1234)
    h.server=NS(spec=O.options(cfg));h.path='/live.jpg';h.reply=Mock()
    h.do_GET();assert h.reply.call_args.args[0]==403
    assert not list(tmp.glob('*.request'))

def test_failure_snapshot_is_whitelisted_and_symlinks_still_rejected(http):
    server,get,_,tmp=http
    server.asset_dir.mkdir(parents=True)
    path=server.asset_dir/'basket_failure.png';path.write_bytes(b'\x89PNG\r\n\x1a\nfailure')
    assert get('/saved/failure.png')[0]==200
    assert json.loads(get('/status.json')[2])['assets']['failure']['name']=='basket_failure.png'
    path.unlink();secret=tmp/'private';secret.write_text('private');path.symlink_to(secret)
    assert get('/saved/failure.png')[0]==404


def test_viewer_failure_does_not_affect_robot(rig,monkeypatch):
    cfg,cam,_,_=rig
    monkeypatch.setattr(V,'Publisher',Mock(side_effect=OSError('RAM unavailable')))
    assert V.attach(cfg,cam) is None
    monkeypatch.setattr('omx_manager.hooks.managed_config',lambda:cfg)
    robot=NS(cameras={'top':cam});V.attach_robot(robot)
    assert robot._omx_view is None


def test_shutdown_only_clears_own_browser_lease(http):
    server,get,_,_=http;get('/status.json')
    path=server.path.with_suffix('.request')
    data=json.loads(path.read_text());data['token']='another-viewer';path.write_text(json.dumps(data))
    server.server_close();assert path.exists()


def test_page_exposes_no_motor_camera_control_or_post_endpoints():
    assert "fetch('/status.json" in V.PAGE and "fetch('/live.jpg" in V.PAGE
    assert 'document.hidden' in V.PAGE and 'URL.revokeObjectURL' in V.PAGE
    assert 'POST' not in V.PAGE and 'confirm-motion' not in V.PAGE
    assert '192.168.0.' in V.ALL


def test_view_setting_edit_preserves_home_models_and_camera_hardware(rig):
    from omx_manager.cli import edit_value
    cfg,*_=rig;cfg.pop('view')
    before=copy.deepcopy(cfg)
    updated=edit_value(cfg,'view.allow_ip','192.168.0.20')
    assert updated['view']['allow_ip']=='192.168.0.20'
    for key in ('hardware','home','models','counting'):assert updated[key]==before[key]
    text=C.annotated_yaml(updated)
    assert '모델 제어 주기와 별개' in text and 'view:' in text


def test_bash_view_shortcut_forwards_config_and_camera_only_from_any_cwd(tmp_path):
    import os,subprocess,shlex,sys
    root=tmp_path/'repo with spaces';(root/'scripts').mkdir(parents=True)
    (root/'scripts/omx_shell.sh').write_bytes((ROOT/'scripts/omx_shell.sh').read_bytes())
    config=root/'state/config/omx4.yaml';config.parent.mkdir(parents=True);config.write_text('placeholder')
    stub=root/'omx_view';stub.write_text('#!/usr/bin/env python3\nimport sys,json,os\nprint(json.dumps(dict(args=sys.argv[1:],python=os.environ.get("OMX_PYTHON"))))\n');stub.chmod(0o755)
    registry=tmp_path/'.config/logistics_omx/shell.env';registry.parent.mkdir(parents=True)
    registry.write_text('OMX_ROBOT_ID=omx4\nOMX_CONFIG_PATH='+shlex.quote(str(config))+'\nOMX_PYTHON='+shlex.quote(sys.executable)+'\n')
    env=dict(os.environ,HOME=str(tmp_path),OMX_ROBOT_ID='omx4',OMX_PYTHON=sys.executable)
    command='source '+shlex.quote(str(root/'scripts/omx_shell.sh'))+'\nomx_view --camera-only'
    result=subprocess.run(['bash','--noprofile','--norc','-c',command],env=env,cwd=tmp_path,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    row=json.loads(result.stdout);assert row['args']==['--config',str(config),'--camera-only']
    assert row['python']==sys.executable


def test_comparison_diagnostic_is_a_whitelisted_saved_asset(http):
    server,get,_,_=http;server.asset_dir.mkdir(parents=True)
    image=server.asset_dir/'basket_compare.png';image.write_bytes(b'comparison-test')
    assert get('/saved/compare.png')[0]==200
    assert get('/saved/compare.png')[2]==image.read_bytes()
    assert json.loads(get('/status.json')[2])['assets']['compare']['name']=='basket_compare.png'
    assert "showSaved('compare')" in V.PAGE


def test_tray_pose_preview_is_whitelisted_without_control_endpoint(http):
    server,get,_,_=http;server.asset_dir.mkdir(parents=True)
    preview=server.asset_dir/'basket_pose.png';preview.write_bytes(b'tray pose preview')
    assert get('/saved/pose.png')[0]==200
    assert json.loads(get('/status.json')[2])['assets']['pose']['name']=='basket_pose.png'
    assert "showSaved('pose')" in V.PAGE
    assert get('/align/on')[0]==404
