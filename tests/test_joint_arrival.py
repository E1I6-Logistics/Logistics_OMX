import copy
import pytest
from omx_manager import config as C
from omx_manager.cli import edit_value
from omx_manager.home import move_home,arrival_tolerances
from omx_manager.simulation import Clock,FakeFollower
from test_manager import cfg,commissioned,q
from test_startup_arming import rig


def test_wrist_only_override_preserves_other_guards_and_binding(commissioned):
    before=copy.deepcopy(commissioned)
    after=edit_value(before,'home.arrival_tolerances.wrist_flex','2.0')
    assert arrival_tolerances(after)['wrist_flex']==2
    assert all(v==.8 for j,v in arrival_tolerances(after).items() if j!='wrist_flex')
    assert after['home']['arrival_tolerance']==before['home']['arrival_tolerance']
    assert after['home']['tolerance']==before['home']['tolerance'] and C.binding(after)==C.binding(before)
    C.require_home(after)


@pytest.mark.parametrize('joint,accept',[('wrist_flex',True),('elbow_flex',False)])
def test_static_measured_offset_only_accepted_for_selected_joint(cfg,joint,accept):
    cfg['home']['arrival_tolerances']={'wrist_flex':2.};cfg['home']['move_timeout_s']=1
    clock=Clock();device=FakeFollower(q());device.q[joint]=1.709
    device.send=lambda _:None
    if accept:
        rows=[];move_home(device,cfg,clock=clock,sleep=clock.sleep,report=rows.append)
        assert rows[-1]['joints']['wrist_flex']['tolerance']==2
        assert rows[-1]['joints']['wrist_flex']['error']==1.709
    else:
        with pytest.raises(TimeoutError):move_home(device,cfg,clock=clock,sleep=clock.sleep)


def test_route_guard_still_uses_strict_waypoint_tolerance(cfg):
    cfg['home']['arrival_tolerances']={'wrist_flex':2};cfg['home']['move_timeout_s']=1
    clock=Clock();device=FakeFollower(q());device.q['wrist_flex']=1.709;device.send=lambda _:None
    with pytest.raises(TimeoutError):move_home(device,cfg,clock=clock,sleep=clock.sleep,guard=lambda _:None)


def test_ready_preflight_uses_same_joint_arrival_override(rig):
    cfg,d,s,path,clock=rig;cfg['home']['arrival_tolerances']={'wrist_flex':2}
    d.torques={j:1 for j in C.JOINTS};d.q['wrist_flex']=1.709
    s.preflight(cfg,verify_registration=False)
    assert not d.events


@pytest.mark.parametrize('value',[True,4,-1,float('nan')])
def test_invalid_override_rejected(cfg,value):
    cfg['home']['arrival_tolerances']={'wrist_flex':value}
    with pytest.raises(C.ConfigError):C.validate(cfg)
