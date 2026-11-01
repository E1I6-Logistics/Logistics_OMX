"""Synthetic process trees + disposable Python children; never live OMX."""
import os
from pathlib import Path
import signal
import subprocess
import sys
from unittest.mock import Mock
import pytest
from omx_manager import stop_all as S
from test_shell_shortcuts import layout, register_shortcuts, shell


def row(pid,args,cwd='/repo',parent=1,uid=1000,start=10,env=None):
    return dict(pid=pid,args=args,cwd=cwd,parent=parent,uid=uid,start=start,env=env or {})


def test_selection_only_omx_repo_same_user_and_descendants():
    rows={
        10:row(10,['sshd'],cwd='/'),
        20:row(20,['bash'],cwd='/repo',parent=10),
        30:row(30,['python','-m','omx_manager.stop_all'],parent=20),
        40:row(40,['python','-m','omx_manager.viewer','--config','/repo/state/config/omx3.yaml']),
        41:row(41,['native-helper'],parent=40),
        42:row(42,['python','-m','lerobot.scripts.lerobot_rollout'],cwd='/repo/lerobot_unified'),
        43:row(43,['python','/repo/lerobot_unified/omx_mqtt_client.py'],cwd='/'),
        50:row(50,['python','unrelated.py']),
        51:row(51,['python','-m','omx_manager.viewer'],cwd='/other'),
        52:row(52,['python','-m','omx_manager.viewer'],uid=2000),
        53:row(53,['python','-c','print("omx_manager.viewer")']),
    }
    assert set(S.select(rows,root='/repo',uid=1000,own=30))=={40,41,42,43}
    assert S.select({50:rows[50]},root='/repo',uid=1000,own=30,tracked={50:9})=={}
    assert set(S.select({41:rows[41]},root='/repo',uid=1000,own=30,tracked={41:10}))=={41}


@pytest.mark.parametrize('module',['omx_manager.cli','omx_manager.viewer','omx_manager.cycle_calibration','omx_manager.recovery_wait'])
def test_module_with_bound_config_outside_cwd(module):
    r=row(40,['python','-m',module,'--config','/repo/state/config/omx1.yaml'],cwd='/home')
    assert S.select({40:r},root='/repo',uid=1000,own=30)


def test_service_stop_failure_prevents_any_signal(monkeypatch):
    monkeypatch.setattr(S,'stop_services',Mock(side_effect=RuntimeError('bus failed')))
    inspect=Mock(side_effect=AssertionError('must not signal/restart live service'))
    monkeypatch.setattr(S,'inventory',inspect)
    with pytest.raises(RuntimeError,match='bus failed'):S.stop_all()
    inspect.assert_not_called()


def test_service_stop_is_explicit_nonblocking_before_process_scan(monkeypatch):
    run=Mock(return_value=type('Result',(),dict(returncode=0,stderr=''))())
    monkeypatch.setattr(S.subprocess,'run',run);S.stop_services()
    assert run.call_args.args[0]==['systemctl','--user','stop','--no-block','omx-manager@*.service']


def test_list_does_not_signal_or_stop_service(monkeypatch):
    r=row(40,['python','-m','omx_manager.viewer'],uid=os.getuid(),cwd=str(S.ROOT))
    monkeypatch.setattr(S,'inventory',lambda:{40:r})
    monkeypatch.setattr(S,'stop_services',Mock(side_effect=AssertionError('read only')))
    monkeypatch.setattr(S,'send',Mock(side_effect=AssertionError('read only')))
    assert S.stop_all(dry_run=True)=={40:r}


@pytest.mark.parametrize('ignore',[False,True])
def test_disposable_child_graceful_or_forced_exit_and_unrelated_child_kept(tmp_path,monkeypatch,ignore):
    script=tmp_path/'omx_control'
    script.write_text('import signal,time\n'+('signal.signal(signal.SIGINT,signal.SIG_IGN)\n' if ignore else '')+'print("READY",flush=True)\ntime.sleep(60)\n')
    target=subprocess.Popen([sys.executable,str(script)],cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],cwd=tmp_path)
    try:
        assert target.stdout.readline().strip()=='READY'
        original=S.select
        monkeypatch.setattr(S,'select',lambda rows,**kw:original(rows,root=tmp_path,**kw))
        monkeypatch.setattr(S,'stop_services',lambda:None)
        assert S.stop_all(timeout=.2)=={}
        target.wait(timeout=3)
        assert target.returncode!=0 and unrelated.poll() is None
        if ignore:assert target.returncode==-signal.SIGKILL
    finally:
        for proc in (target,unrelated):
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=3)
            if proc.stdout:proc.stdout.close()


def test_recycled_identity_is_not_signaled():
    proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    try:
        r=S.read_process(proc.pid);assert r is not None;r['start']-=1
        assert not S.send(r,signal.SIGKILL) and proc.poll() is None
    finally:proc.kill();proc.wait(timeout=3)


def test_shortcut_works_from_any_cwd_without_yaml_read(layout):
    register_shortcuts(layout,'omx3')
    result=shell(layout,'OMX_PYTHON=/bin/echo\nOMX_CONFIG_PATH=/missing.yaml\ncd /\nomx_kill --list')
    assert result.returncode==0 and '-m omx_manager.stop_all --list' in result.stdout
