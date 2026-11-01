"""Explicit direct return from distant poses, without hardware or collision claims."""
import copy
import pytest
from omx_manager import config as C,hardware as H,runtime as R
from omx_manager.home import move_home
from omx_manager.return_path import return_home
from omx_manager.messages import korean
from omx_manager.simulation import Clock,FakeFollower
from test_manager import cfg,commissioned,q
from test_startup_arming import rig


@pytest.mark.parametrize('cfg',['omx1','omx2','omx3','omx4'],indirect=True)
def test_cold_start_at_distant_valid_pose_latches_then_returns_without_resetting_yaml(rig):
    cfg,d,s,path,clock=rig;cfg['recovery']['direct_home']=True
    d.q.update(shoulder_pan=-40,shoulder_lift=-30,elbow_flex=70,wrist_flex=15,wrist_roll=-10,gripper=25)
    before=copy.deepcopy(cfg);start=d.q.copy()
    s.preflight(cfg,verify_registration=False)
    assert d.events[0]==('goal',start) and d.events[1][0]=='enable'
    assert max(abs(d.q[j]-cfg['home']['target'][j]) for j in C.JOINTS)<=cfg['home']['arrival_tolerance']
    goals=[values for name,values in d.events if name=='goal']
    for a,b in zip(goals,goals[1:]):assert max(abs(a[j]-b[j]) for j in C.JOINTS)<=cfg['home']['max_step']+1e-8
    assert cfg==before


def test_default_still_rejects_unknown_pose_without_motor_writes(cfg):
    clock=Clock();device=FakeFollower(q(70))
    with pytest.raises(C.ConfigError,match='No unique tested'):return_home(device,cfg,clock=clock,sleep=clock.sleep)
    assert device.sent==[]


def test_direct_error_return_preserves_gripper(cfg):
    cfg['recovery']['direct_home']=True;clock=Clock();device=FakeFollower(q(40))
    result=return_home(device,cfg,clock=clock,sleep=clock.sleep,preserve_gripper=True)
    assert result['gripper']==40 and all(command['gripper']==40 for command in device.sent)
    assert all(abs(result[j])<=cfg['home']['arrival_tolerance'] for j in C.JOINTS if j!='gripper')


def test_direct_mode_does_not_disable_stop_feedback_or_following_guards(cfg):
    cfg['recovery']['direct_home']=True;clock=Clock();device=FakeFollower(q(60))
    with pytest.raises(InterruptedError):move_home(device,cfg,clock=clock,sleep=clock.sleep,stopped=lambda:True)
    assert not device.sent
    # A jammed arm must never be driven with goals growing beyond feedback lag.
    commands=[];device.send=lambda values:commands.append(values.copy())
    with pytest.raises(TimeoutError):move_home(device,cfg,clock=clock,sleep=clock.sleep)
    assert all(max(abs(v-60) for v in row.values())<=cfg['home']['max_following_error']+1e-8 for row in commands)


def test_direct_start_never_energizes_faulty_or_moving_motors(rig):
    cfg,d,s,path,clock=rig;cfg['recovery']['direct_home']=True;d.q['elbow_flex']=70
    d.errors['elbow_flex']=4
    with pytest.raises(RuntimeError):s.preflight(cfg,verify_registration=False)
    assert not d.events


def test_display_translates_operator_errors_and_retains_protocol_codes():
    assert '홈 복귀 제한 시간 초과' in korean('Home movement timed out: wrist_roll error=2.1')
    assert korean('[STATE] READY')=='[STATE] READY · 주문 대기 준비 완료'
    assert '토크 OFF' in korean('Torque OFF: shoulder_pan; tool will not enable it automatically')


def test_direct_config_is_opt_in_and_strictly_boolean(cfg):
    assert cfg['recovery']['direct_home'] is False
    cfg['recovery']['direct_home']='yes'
    with pytest.raises(C.ConfigError):C.validate(cfg)
