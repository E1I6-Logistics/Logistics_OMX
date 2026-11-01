"""Two camera channels and robot tabs; fake RGB only, no hardware."""
import copy,json,subprocess,threading,time,re,shutil
from pathlib import Path
from types import SimpleNamespace as NS
from http.client import HTTPConnection
from unittest.mock import Mock
import numpy as np
import pytest
from omx_manager import viewer as V,config as C,view_options as O


@pytest.fixture
def dual(tmp_path,monkeypatch):
    cfg=C.load(Path(__file__).resolve().parents[1]/'params/omx4.yaml')
    monkeypatch.setattr(V,'ROOT',tmp_path)
    monkeypatch.setattr(V,'channel',lambda c:tmp_path/O.camera_name(c))
    def cam(color):return NS(read_latest_with_timestamp=lambda **kw:(np.full((24,32,3),color,np.uint8),time.perf_counter()))
    pubs={name:V.Publisher(V.camera_config(cfg,name),cam(color)) for name,color in [('top',50),('wrist',180)]}
    server=V.ViewerServer(cfg,('127.0.0.1',0));thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
    def get(path):
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        try:conn.request('GET',path);r=conn.getresponse();return r.status,r.read()
        finally:conn.close()
    yield cfg,server,pubs,get
    server.shutdown();server.server_close();thread.join(timeout=1)


def test_request_one_camera_never_activates_other_encoder(dual):
    cfg,s,p,get=dual
    assert get('/live.jpg?camera=wrist')[0]==503
    assert p['wrist'].step() and not p['top'].step()
    row=json.loads(get('/status.json?camera=wrist')[1])
    assert row['live'] and row['frame']['camera']=='wrist'
    assert not row['cameras']['top']['live']


def test_both_channels_are_independent_and_backward_default_still_works(dual):
    cfg,s,p,get=dual;get('/status.json?camera=all')
    assert all(pub.step() for pub in p.values())
    result=json.loads(get('/status.json?camera=all')[1]);assert all(r['live'] for r in result['cameras'].values())
    images=[get('/live.jpg?camera='+name)[1] for name in p];assert images[0]!=images[1]
    assert get('/live.jpg')[1]==images[0]
    p['wrist'].path.with_suffix('.frame').unlink()
    assert get('/live.jpg?camera=top')[0]==200 and get('/live.jpg?camera=wrist')[0]==503
    assert get('/live.jpg?camera=../../etc/passwd')[0]==404


def test_cross_camera_packet_never_served_as_requested_camera(dual):
    _,s,p,get=dual;get('/status.json?camera=all');p['top'].step()
    p['wrist'].path.with_suffix('.frame').write_bytes(p['top'].path.with_suffix('.frame').read_bytes())
    assert get('/live.jpg?camera=wrist')[0]==503


def test_attach_robot_reuses_both_existing_cameras_and_closes_independently(dual,monkeypatch):
    cfg,*_=dual;before=copy.deepcopy(cfg);cameras={'top':object(),'wrist':object()};items=[]
    def attach(c,cam):
        assert cam is cameras[O.camera_name(c)]
        p=NS(close=Mock());items.append(p);return p
    monkeypatch.setattr('omx_manager.hooks.managed_config',lambda:cfg);monkeypatch.setattr(V,'attach',attach)
    robot=NS(cameras=cameras);V.attach_robot(robot);assert len(items)==2 and cfg==before
    items[0].close.side_effect=RuntimeError('cleanup failed');robot._omx_view.close()
    assert all(p.close.call_count==1 for p in items)


@pytest.mark.skipif(shutil.which('node') is None,reason='JavaScript 문법/DOM 시험에는 개발 PC의 node가 필요합니다')
def test_pages_parse_as_javascript_and_only_selected_robot_frames_exist():
    for page in (V.PAGE,V.ALL):
        script=re.search(r'<script>(.*?)</script>',page,re.S)[1]
        subprocess.run(['node','--check'],input=script,text=True,check=True,capture_output=True)
    # Execute the real tab script with a tiny DOM. No network or browser libs.
    harness=r'''
class Element{constructor(){this.children=[];this.dataset={};this.value='';this.attrs={};this.classList={toggle(){}};}append(...items){this.children.push(...items);}replaceChildren(){this.children=[];}setAttribute(k,v){this.attrs[k]=v;}}
const nodes={};global.document={createElement:()=>new Element(),querySelector:(s)=>nodes[s]||(nodes[s]=new Element())};global.location={port:'8080'};
'''
    script=re.search(r'<script>(.*?)</script>',V.ALL,re.S)[1]
    checks=r'''
const assert=require('assert');assert.equal(tabs.children.length,4);for(let i=0;i<4;i++){tabs.children[i].onclick();assert.equal(grid.children.length,1);assert(grid.children[0].src.endsWith('.'+(151+i)+':8080/robot'));}
'''
    # querySelector must resolve dynamically assigned input IDs in this tiny DOM.
    harness=harness.replace('append(...items){this.children.push(...items);}',"append(...items){this.children.push(...items);for(const i of items){if(i.id)nodes['#'+i.id]=i;}}")
    subprocess.run(['node'],input=harness+script+checks,text=True,check=True,capture_output=True)


def test_root_and_all_are_tabs_but_robot_route_is_single_stream_page(dual):
    _,s,p,get=dual
    for path in ('/','/all'):
        code,data=get(path);assert code==200 and b'id="tabs"' in data
        assert b"fetch(" not in data
    code,data=get('/robot');assert code==200 and b'id="cameras"' in data
    assert b'AbortController' in data and b"pagehide" in data
    assert not any(pub.path.with_suffix('.request').exists() for pub in p.values())


@pytest.mark.parametrize('address,allowed',[('192.168.0.16',True),('192.168.0.77',True),('10.10.0.20',True)])
def test_any_ip_can_read_page_without_camera_access(dual,address,allowed):
    cfg,s,p,get=dual
    updated=dict(cfg,view=dict(cfg['view'],allow_ip='any'));C.validate(updated)
    handler=object.__new__(V.ViewerHandler);handler.client_address=(address,1234);handler.path='/'
    handler.server=NS(spec=O.options(updated));handler.reply=Mock()
    handler.do_GET();assert handler.reply.call_args.args[0]==200
    assert not any(pub.path.with_suffix('.request').exists() for pub in p.values())


def test_any_ip_edit_preserves_model_and_specific_ip_rule(dual):
    from omx_manager.cli import edit_value
    cfg,*_=dual;updated=edit_value(cfg,'view.allow_ip','any')
    assert updated['view']['allow_ip']=='any' and updated['hardware']==cfg['hardware']
    assert updated['home']==cfg['home'] and updated['models']==cfg['models']
    assert edit_value(updated,'view.allow_ip','192.168.0.16')['view']['allow_ip']=='192.168.0.16'
