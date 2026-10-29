"""Service rendering/control are mocked; no actual systemd install/start occurs."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from omx_manager import config as C, service as S
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def cfg():return C.load(ROOT/'params/omx1.yaml')

@pytest.fixture
def calls(monkeypatch):
    run=Mock(return_value=SimpleNamespace(returncode=0));monkeypatch.setattr(S.subprocess,'run',run)
    return run

def test_unit_has_supervisor_crash_and_oom_restart_policy(cfg,tmp_path):
    text=S.render(cfg,tmp_path/'params.yaml',root=tmp_path/'OMX repo')
    assert 'Restart=always' in text and 'RestartSec=5.0' in text
    assert 'OOMPolicy=stop' in text and 'KillMode=control-group' in text
    assert 'TimeoutStopSec=45.0' in text and 'StartLimitIntervalSec=0' in text
    assert '"'+str(tmp_path/'OMX repo'/'run_omx_mqtt.sh')+'"' in text
    assert '--important-only' in text and '--diagnostics' not in text
    assert '--robot-id omx1 --config' in text
    assert '@OMX_' not in text

def test_install_creates_unit_but_never_starts_hardware(cfg,tmp_path,calls):
    S.manage(cfg,ROOT/'params/omx1.yaml','install',unit_dir=tmp_path)
    assert (tmp_path/'omx-manager@omx1.service').exists()
    assert [c.args[0][2:] for c in calls.call_args_list]==[['daemon-reload'],['disable','omx-manager@omx1.service']]

def test_install_boot_enable_needs_confirmation_before_writing(cfg,tmp_path,calls):
    cfg['service']['enable_on_boot']=True
    with pytest.raises(ValueError,match='confirm-motion'):S.manage(cfg,ROOT/'params/omx1.yaml','install',unit_dir=tmp_path)
    assert not list(tmp_path.iterdir());calls.assert_not_called()
    S.manage(cfg,ROOT/'params/omx1.yaml','install',True,unit_dir=tmp_path)
    assert calls.call_args.args[0][2:] == ['enable','omx-manager@omx1.service']

@pytest.mark.parametrize('action',['start','enable'])
def test_motion_service_actions_are_explicit_and_checked(cfg,tmp_path,calls,monkeypatch,action):
    with pytest.raises(ValueError,match='confirm-motion'):S.manage(cfg,ROOT/'params/omx1.yaml',action,unit_dir=tmp_path)
    S.manage(cfg,ROOT/'params/omx1.yaml','install',unit_dir=tmp_path);calls.reset_mock()
    files=Mock();home=Mock();monkeypatch.setattr(S,'check_files',files);monkeypatch.setattr(S,'require_home',home)
    S.manage(cfg,ROOT/'params/omx1.yaml',action,True,unit_dir=tmp_path)
    files.assert_called_once();home.assert_called_once()
    calls.assert_called_once_with(['systemctl','--user',action,'omx-manager@omx1.service'],check=True)

def test_manual_stop_does_not_issue_restart_or_enable(cfg,tmp_path,calls):
    S.manage(cfg,ROOT/'params/omx1.yaml','stop',unit_dir=tmp_path)
    calls.assert_called_once_with(['systemctl','--user','stop','omx-manager@omx1.service'],check=True)

def test_custom_unit_is_preserved(cfg,tmp_path,calls):
    file=tmp_path/'omx-manager@omx1.service';file.write_text('# my custom unit')
    for action in ('install','uninstall'):
        with pytest.raises(ValueError):S.manage(cfg,ROOT/'params/omx1.yaml',action,unit_dir=tmp_path)
    assert file.read_text()=='# my custom unit';calls.assert_not_called()

def test_uninstall_stops_and_disables_only_selected_unit(cfg,tmp_path,calls):
    S.manage(cfg,ROOT/'params/omx1.yaml','install',unit_dir=tmp_path);calls.reset_mock()
    S.manage(cfg,ROOT/'params/omx1.yaml','uninstall',unit_dir=tmp_path)
    assert not (tmp_path/'omx-manager@omx1.service').exists()
    assert calls.call_args_list[0].args[0][2:]==['disable','--now','omx-manager@omx1.service']

def test_status_inactive_is_reported_without_exception(cfg,tmp_path,calls):
    calls.return_value.returncode=3
    S.manage(cfg,ROOT/'params/omx1.yaml','status',unit_dir=tmp_path)
    assert calls.call_args.kwargs['check'] is False

def test_short_shutdown_budget_is_rejected(cfg,tmp_path):
    cfg['service']['stop_timeout_s']=10
    with pytest.raises(ValueError,match='shutdown_timeout'):S.render(cfg,tmp_path/'params.yaml')

def test_unit_escapes_paths_and_rejects_newline_injection(cfg,tmp_path):
    text=S.render(cfg,tmp_path/'100% $cfg.yaml',root=tmp_path/'100% $repo')
    assert '100%% $$repo/run_omx_mqtt.sh' in text
    with pytest.raises(ValueError):S.quote('/bad\nExecStart=anything')
