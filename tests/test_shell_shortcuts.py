"""Bash registration in temporary homes; fake commands only, no robot/network."""
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omx_manager import config as C
from omx_manager import hardware as H
from omx_manager import orders as O
from omx_manager import shell_setup as S
from omx_manager import cli

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "OMX repo $literal 'quoted'"
    home = tmp_path / 'home'
    home.mkdir()
    (root / 'scripts').mkdir(parents=True)
    (root / 'params').mkdir()
    for number in range(1,5):
        shutil.copyfile(ROOT/f'params/omx{number}.yaml', root/f'params/omx{number}.yaml')
    shutil.copyfile(ROOT/'scripts/omx_shell.sh', root/'scripts/omx_shell.sh')
    shutil.copyfile(ROOT/'run_omx_mqtt.sh', root/'run_omx_mqtt.sh')
    stub = root/'omx_control'
    stub.write_text('#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd(),"python":os.environ.get("OMX_PYTHON")}))\n')
    stub.chmod(0o755)
    (root/'run_omx_mqtt.sh').chmod(0o755)
    return root,home


@pytest.mark.parametrize('hostname,expected', [('omx01','omx1'),('omx02','omx2'),('omx03','omx3'),('omx4','omx4'),('OMX04.local','omx4'),('unknown',None)])
def test_hostname_mapping(hostname,expected):
    assert S.robot_from_hostname(hostname) == expected


def register_shortcuts(layout, device='omx4', **kwargs):
    root,home=layout
    return S.install(device, python=sys.executable, root=root, home=home, hostname=device, **kwargs)


def shell(layout,command):
    root,home=layout
    env=dict(os.environ,HOME=str(home))
    for key in ('OMX_ROBOT_ID','OMX_CONFIG_PATH','OMX_PYTHON','OMX_PYTHON_ENV_PATH'):
        env.pop(key,None)
    text='source '+shlex.quote(str(root/'scripts/omx_shell.sh'))+'\n'+command
    return subprocess.run(['bash','--noprofile','--norc','-c',text],env=env,cwd=home,text=True,capture_output=True)


def test_install_preserves_home_models_and_bashrc_and_is_idempotent(layout):
    root,home=layout
    (home/'.bashrc').write_text('export KEEP_ME=hello\nsource "$HOME/Logistics_OMX/scripts/omx_shell.sh" # Logistics_OMX shell integration\n')
    existing=root/'state/config/omx4.yaml'
    existing.parent.mkdir(parents=True)
    cfg=C.load(root/'params/omx4.yaml');cfg['home']['target']={j:5. for j in C.JOINTS}
    C.save(existing,cfg)
    before=existing.read_bytes()
    first=register_shortcuts(layout)
    rc=(home/'.bashrc').read_text();registration=first['registration'].read_bytes()
    assert 'export KEEP_ME=hello' in rc
    assert 'Logistics_OMX shell integration' not in rc
    assert rc.count(S.BEGIN)==1
    assert existing.read_bytes()==before
    backups=list(home.glob('.bashrc.omx-*.bak'))
    register_shortcuts(layout)
    assert (home/'.bashrc').read_text()==rc
    assert first['registration'].read_bytes()==registration
    assert list(home.glob('.bashrc.omx-*.bak'))==backups
    assert (first['registration'].stat().st_mode&0o777)==0o600
    assert shell(layout, ':').stdout==''
    assert shell(layout, ':').returncode==0


def test_auto_detects_pi_and_preserves_existing_python_symlink(layout):
    root,home=layout
    executable=home/'venv/bin/python'
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    result=S.install('auto',python=str(executable),root=root,home=home,hostname='omx03')
    assert result['robot_id']=='omx3'
    assert str(result['python'])==str(executable)
    assert result['config']==root/'state/config/omx3.yaml'


def test_unknown_or_conflicting_identity_does_not_write(layout):
    root,home=layout
    with pytest.raises(ValueError,match='identify'):
        S.install(root=root,home=home,hostname='workstation')
    assert not (home/'.bashrc').exists()
    legacy=home/'.config/logistics_omx/device.env'
    legacy.parent.mkdir(parents=True)
    legacy.write_text('OMX_ROBOT_ID=omx2\n')
    with pytest.raises(ValueError,match='mismatch'):
        S.install(root=root,home=home,hostname='omx01')
    assert legacy.read_text()=='OMX_ROBOT_ID=omx2\n'
    assert not (home/'.bashrc').exists()


def test_custom_registration_and_missing_registered_config_are_preserved(layout):
    root,home=layout
    file=home/'.config/logistics_omx/shell.env'
    file.parent.mkdir(parents=True)
    file.write_text('# hand-written configuration\n')
    with pytest.raises(ValueError,match='Custom'):register_shortcuts(layout)
    assert file.read_text()=='# hand-written configuration\n'
    file.unlink()
    result=register_shortcuts(layout)
    result['config'].unlink()
    with pytest.raises(ValueError,match='config missing'):register_shortcuts(layout)


def test_explicit_yaml_mismatch_is_rejected_before_registration(layout):
    root,home=layout
    with pytest.raises(ValueError,match='device_id'):
        S.install('omx1',root/'params/omx2.yaml',sys.executable,root=root,home=home)
    assert not (home/'.bashrc').exists()


def test_bashrc_symlink_is_retained(layout):
    root,home=layout
    target=home/'dotfiles/rc'
    target.parent.mkdir()
    target.write_text('export KEEP=yes\n')
    (home/'.bashrc').symlink_to(target)
    register_shortcuts(layout)
    assert (home/'.bashrc').is_symlink()
    assert 'export KEEP=yes' in target.read_text()
    assert S.BEGIN in target.read_text()


@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
def test_shortcuts_select_device_and_local_yaml_without_changing_cwd(layout,device):
    root,home=layout
    result=register_shortcuts(layout,device)
    proc=shell(layout,'omx_home')
    assert proc.returncode==0,proc.stderr
    output=json.loads(proc.stdout)
    assert output['args']==['--config',str(result['config']),'--expected-device',device,'home','--confirm-motion']
    assert output['cwd']==str(home)
    assert output['python']==sys.executable
    start=shell(layout,'omx_start --diagnostics')
    assert start.returncode==0,start.stderr
    args=json.loads(start.stdout.splitlines()[-1])['args']
    assert args[:3]==['--config',str(result['config']),'run']
    assert '--confirm-motion' in args and '--important-only' in args and '--diagnostics' in args
    assert args[-2:]==['--expected-device',device]


@pytest.mark.parametrize('command,expected',[
    ('omx_arm',['arm','--confirm-motion']),('omx_release',['release','--confirm-motion']),
    ('omx_observe',['observe']),('omx_capture',['capture']),('omx_check',['check']),
    ('omx_calibrate',['menu']),('omx_logs --follow',['logs','--follow']),
    ('omx_monitor --samples 2',['monitor','--samples','2']),('omx_status',['monitor','--samples','1']),
    ('omx_recover',['acknowledge-recovery','--confirm-motion']),
    ('omx_order F 1',['order','--item','F','--quantity','1']),
    ('omx_service stop',['service','--action','stop']),
    ('omx_service start',['service','--action','start','--confirm-motion']),
])
def test_each_shortcut_forwards_only_expected_arguments(layout,command,expected):
    register_shortcuts(layout)
    proc=shell(layout,command)
    assert proc.returncode==0,proc.stderr
    assert json.loads(proc.stdout)['args'][4:]==expected


def test_empty_order_does_not_run_anything_and_source_removes_legacy_reset(layout):
    root,home=layout
    register_shortcuts(layout)
    proc=shell(layout,'omx_order')
    assert proc.returncode==2 and not proc.stdout
    text='omx_reset() { echo OLD_RESET; }\nalias omx_home="echo OLD_HOME"\nsource '+shlex.quote(str(root/'scripts/omx_shell.sh'))+'\ndeclare -F omx_reset || true\nomx_home'
    proc=subprocess.run(['bash','--noprofile','--norc','-c',text],cwd=home,env=dict(os.environ,HOME=str(home)),capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr
    assert 'OLD_' not in proc.stdout
    assert json.loads(proc.stdout)['args'][-2:]==['home','--confirm-motion']


@pytest.mark.parametrize('device,item',[('omx1','A'),('omx1','B'),('omx2','C'),('omx2','D'),('omx3','E'),('omx4','F')])
def test_order_uses_cli_qos_one_unique_ids_and_no_retain(monkeypatch,device,item):
    cfg=C.load(ROOT/f'params/{device}.yaml')
    run=Mock();monkeypatch.setattr(O.subprocess,'run',run);monkeypatch.setattr(O.shutil,'which',lambda _: '/fake/mosquitto_pub')
    first=O.send_order(cfg,item,1);second=O.send_order(cfg,item,1)
    assert first['job_id']!=second['job_id']
    args=run.call_args.args[0]
    assert args[:7]==['/fake/mosquitto_pub','-h',cfg['runtime']['broker_host'],'-p',str(cfg['runtime']['broker_port']),'-t',device+'/command']
    assert '-r' not in args and '--retain' not in args
    assert args[args.index('-q')+1]=='1'
    assert json.loads(args[args.index('-m')+1])['items']=={item:1}
    assert run.call_args.kwargs=={'check':True}


@pytest.mark.parametrize('item,quantity',[('X',1),('A',0),('A',True),('A',9),(None,1)])
def test_bad_manual_order_never_publishes(monkeypatch,item,quantity):
    run=Mock();monkeypatch.setattr(O.subprocess,'run',run)
    with pytest.raises(ValueError): O.send_order(C.load(ROOT/'params/omx1.yaml'),item,quantity)
    run.assert_not_called()


def test_unload_quantity_and_missing_cli_are_explicit(monkeypatch):
    run=Mock();monkeypatch.setattr(O.subprocess,'run',run)
    cfg=C.load(ROOT/'params/omx4.yaml')
    monkeypatch.setattr(O.shutil,'which',lambda _:'/fake/mosquitto_pub')
    assert O.send_order(cfg,'F',2)['items']=={'F':2}
    run.assert_called_once();run.reset_mock()
    monkeypatch.setattr(O.shutil,'which',lambda _:None)
    with pytest.raises(ValueError,match='mosquitto_pub'):O.send_order(cfg,'F',1)
    run.assert_not_called()


def test_release_writes_only_torque_and_checks_all_six_motors():
    driver=H.FollowerBus({})
    driver.bus=Mock()
    driver.bus.read.return_value=0
    states=driver.release()
    driver.bus.disable_torque.assert_called_once_with(num_retry=3)
    assert states=={j:0 for j in C.JOINTS}
    assert driver.bus.read.call_count==6
    driver.bus.write.assert_not_called()
    driver.bus.sync_write.assert_not_called()
    driver.bus.read.return_value=1
    with pytest.raises(RuntimeError,match='verification'):driver.release()


def test_release_cli_requires_confirmation_and_allows_unregistered_far_pose(tmp_path,monkeypatch):
    from contextlib import nullcontext
    cfg=C.load(ROOT/'params/omx4.yaml')
    path=tmp_path/'omx4.yaml';C.save(path,cfg)
    bus=Mock();bus.__enter__=Mock(return_value=bus);bus.__exit__=Mock(return_value=False)
    bus.release.return_value={j:0 for j in C.JOINTS}
    factory=Mock(return_value=bus)
    monkeypatch.setattr(cli,'device_lock',lambda _:nullcontext())
    monkeypatch.setattr(cli,'FollowerBus',factory)
    sleep=Mock();monkeypatch.setattr(cli.time,'sleep',sleep)
    with pytest.raises(ValueError,match='confirm-motion'):cli.invoke(path,'release')
    factory.assert_not_called()
    cli.invoke(path,'release',confirm_motion=True)
    sleep.assert_called_once_with(10)
    bus.release.assert_called_once()
    bus.arm.assert_not_called()
    assert C.load(path)==cfg


def test_release_interrupt_does_not_disable_torque(tmp_path,monkeypatch):
    from contextlib import nullcontext
    cfg=C.load(ROOT/'params/omx4.yaml');path=tmp_path/'omx4.yaml';C.save(path,cfg)
    bus=Mock();bus.__enter__=Mock(return_value=bus);bus.__exit__=Mock(return_value=False)
    monkeypatch.setattr(cli,'device_lock',lambda _:nullcontext())
    monkeypatch.setattr(cli,'FollowerBus',lambda _:bus)
    monkeypatch.setattr(cli.time,'sleep',Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):cli.invoke(path,'release',confirm_motion=True)
    bus.release.assert_not_called()
    bus.__exit__.assert_called_once()


@pytest.mark.parametrize('delay',[0,2,True,61,float('nan')])
def test_release_delay_validation(delay):
    cfg=C.load(ROOT/'params/omx4.yaml');cfg['recovery']['release_delay_s']=delay
    with pytest.raises(C.ConfigError):C.validate(cfg)
