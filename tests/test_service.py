"""Mock service actions plus real offline systemd parser; no service is started."""
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
    assert [c.args[0][2:] for c in calls.call_args_list if c.args[0][0]=='systemctl']==[['daemon-reload'],['disable','omx-manager@omx1.service']]

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


def test_working_directory_uses_literal_path_and_only_escapes_specifiers(tmp_path):
    root=tmp_path/'OMX repo $literal 100% "quoted" \\backslash'
    assert S.working_directory(root)==str(root).replace('%','%%')
    for value in ('relative/path','/bad\nExecStart=anything','/bad\tpath','/trailing ','/trailing\\'):
        with pytest.raises(ValueError):S.working_directory(value)


@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
@pytest.mark.parametrize('special',[False,True])
def test_generated_unit_passes_real_systemd_parser_without_executing_it(tmp_path,device,special):
    import shutil,subprocess,shlex
    analyzer=shutil.which('systemd-analyze')
    if analyzer is None:pytest.skip('systemd-analyze not installed')
    root=tmp_path/('OMX repo $literal 100% "quoted" \\backslash' if special else 'Logistics_OMX')
    root.mkdir();marker=tmp_path/'EXECUTED'
    executable=root/'run_omx_mqtt.sh'
    executable.write_text('#!/bin/sh\ntouch '+shlex.quote(str(marker))+'\nexit 0\n');executable.chmod(0o755)
    cfg=C.load(ROOT/f'params/{device}.yaml')
    text=S.render(cfg,tmp_path/'config file $literal 100%.yaml',root=root)
    candidate=tmp_path/f'omx-manager@{device}.service';candidate.write_text(text)
    result=subprocess.run([analyzer,'verify','--man=no',str(candidate)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert not marker.exists() # offline syntax check, no command/motor execution
    directory=next(row for row in text.splitlines() if row.startswith('WorkingDirectory='))
    assert directory=='WorkingDirectory='+str(root).replace('%','%%')


def test_real_parser_rejects_old_quoted_directory_before_install(cfg,tmp_path):
    import shutil
    if shutil.which('systemd-analyze') is None:pytest.skip('systemd-analyze not installed')
    root=tmp_path/'repo';root.mkdir();script=root/'run_omx_mqtt.sh'
    script.write_text('#!/bin/sh\nexit 0\n');script.chmod(0o755)
    text=S.render(cfg,tmp_path/'cfg.yaml',root=root)
    old=text.replace('WorkingDirectory='+S.working_directory(root),'WorkingDirectory='+S.quote(root))
    with pytest.raises(ValueError,match='not absolute|bad unit file setting|fatal error'):
        S.verify_unit(old,'omx-manager@omx1.service')


def test_failed_candidate_verification_preserves_installed_unit(cfg,tmp_path,calls,monkeypatch):
    target=tmp_path/'omx-manager@omx1.service';original=S.MARKER+' previous unit\n'
    target.write_text(original)
    monkeypatch.setattr(S,'verify_unit',Mock(side_effect=ValueError('invalid candidate')))
    with pytest.raises(ValueError,match='invalid candidate'):
        S.manage(cfg,ROOT/'params/omx1.yaml','install',unit_dir=tmp_path)
    assert target.read_text()==original and not list(tmp_path.glob('*.bak'))
    calls.assert_not_called()


def test_service_status_never_opens_pager_or_truncates_errors(cfg,tmp_path,calls):
    S.manage(cfg,ROOT/'params/omx1.yaml','status',unit_dir=tmp_path)
    argv=calls.call_args.args[0]
    assert '--no-pager' in argv and '--full' in argv
