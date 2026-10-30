"""Home approaches with deadband, actuator lag and frozen feedback; no hardware."""
import copy
from pathlib import Path

import pytest

from omx_manager import config as C
from omx_manager.home import HomeDetector, move_home
from omx_manager.simulation import Clock, FakeFollower

ROOT = Path(__file__).resolve().parents[1]


def pose(value):
    return {joint: float(value) for joint in C.JOINTS}


@pytest.fixture
def cfg():
    cfg = C.load(ROOT / 'params/omx1.yaml')
    cfg['home']['target'] = pose(0)
    cfg['counting']['min_cycle_s'] = 0
    cfg['home']['max_following_error'] = 3.0
    return cfg


class DeadbandFollower(FakeFollower):
    def __init__(self, start=10, deadband=.4, response=1.):
        super().__init__(pose(start))
        self.deadband = deadband
        self.response = response
        self.feedback = []

    def send(self, command):
        self.sent.append(command.copy())
        self.feedback.append(self.q.copy())
        for joint in C.JOINTS:
            delta = command[joint] - self.q[joint]
            if abs(delta) >= self.deadband:
                self.q[joint] += self.response * delta


@pytest.mark.parametrize('deadband,response', [(0., 1.), (.2, 1.), (.4, 1.), (.7, 1.), (0., .05), (.4, .1)])
def test_accumulated_goals_reach_home_with_actuator_deadband_and_lag(cfg, deadband, response):
    clock = Clock()
    device = DeadbandFollower(deadband=deadband, response=response)
    result = move_home(device, cfg, clock=clock, sleep=clock.sleep)
    assert max(result.values()) <= cfg['home']['arrival_tolerance']
    previous = pose(10)
    for command, observed in zip(device.sent, device.feedback):
        for joint in C.JOINTS:
            assert 0 <= command[joint] <= previous[joint]
            assert previous[joint] - command[joint] <= cfg['home']['max_step'] + 1e-9
            assert abs(command[joint] - observed[joint]) <= cfg['home']['max_following_error'] + 1e-9
        previous = command


def test_frozen_arm_never_builds_unbounded_goal_error(cfg):
    clock = Clock()
    device = DeadbandFollower(deadband=100)
    cfg['home']['move_timeout_s'] = 3
    with pytest.raises(TimeoutError):
        move_home(device, cfg, clock=clock, sleep=clock.sleep)
    assert max(abs(command[joint] - 10) for command in device.sent for joint in C.JOINTS) <= 3.0 + 1e-9
    assert device.q == pose(10)


def test_home_timeout_identifies_the_blocking_joint(cfg):
    clock = Clock()
    device = DeadbandFollower(deadband=100)
    cfg['home']['move_timeout_s'] = 1
    with pytest.raises(TimeoutError, match=r'shoulder_pan.*error=10'):
        move_home(device, cfg, clock=clock, sleep=clock.sleep)


def test_feedback_leaving_approved_approach_area_stops_commands(cfg):
    clock = Clock()
    device = FakeFollower(pose(10))
    def read():
        device.q = {joint: value + .3 for joint, value in device.q.items()}
        return device.q.copy()
    device.read = read
    device.send = lambda command: device.sent.append(command.copy())
    with pytest.raises((C.ConfigError, RuntimeError), match='(approach|following)'):
        move_home(device, cfg, clock=clock, sleep=clock.sleep)


def test_home_report_records_final_joint_errors_without_camera_or_file_io(cfg):
    clock = Clock()
    device = DeadbandFollower(deadband=.4)
    reports = []
    result = move_home(device, cfg, clock=clock, sleep=clock.sleep, report=reports.append)
    assert reports[0]['phase'] == 'STARTING'
    assert reports[-1]['phase'] == 'ARRIVED'
    assert reports[-1]['joints']['wrist_flex']['current'] == result['wrist_flex']
    assert all(row['ok'] for row in reports[-1]['joints'].values())
    moving = [row for row in reports if row['phase'] == 'MOVING']
    assert len(moving) < clock() + 2


def test_home_report_failures_do_not_change_motion(cfg):
    clock = Clock()
    device = DeadbandFollower(deadband=.4)
    def broken_report(data):
        raise OSError('diagnostic storage full')
    result = move_home(device, cfg, clock=clock, sleep=clock.sleep, report=broken_report)
    assert max(result.values()) <= cfg['home']['arrival_tolerance']


def test_detector_exposes_home_window_after_slow_inference_without_counting_gap(cfg):
    detector = HomeDetector(cfg)
    detector.update(pose(20), .05)
    assert not detector.update(pose(0), 2.05)
    assert detector.in_home_window
    assert detector.since is None
    assert detector.departed
    counts = sum(detector.update(pose(0), 2.05 + index * .05) for index in range(1, 15))
    assert counts == 1


def test_home_window_does_not_accept_wrong_joint_or_initial_idle(cfg):
    detector = HomeDetector(cfg)
    detector.update(pose(0), .05)
    assert not detector.in_home_window
    detector.update(pose(20), .10)
    wrong = pose(0)
    wrong['wrist_flex'] = 7.863
    detector.update(wrong, .15)
    assert not detector.in_home_window
    assert not detector.rows['wrist_flex']['ok']


def test_optional_following_limit_is_validated_and_existing_yaml_remains_supported(cfg):
    legacy = copy.deepcopy(cfg)
    legacy['home'].pop('max_following_error')
    C.validate(legacy)
    C.validate(cfg)
    for value in (0, -1, True, float('nan'), 31):
        bad = copy.deepcopy(cfg)
        bad['home']['max_following_error'] = value
        with pytest.raises(C.ConfigError):
            C.validate(bad)


@pytest.mark.parametrize('device_id', ['omx1', 'omx2', 'omx3', 'omx4'])
def test_repeated_episode_starts_use_joint_feedback_only(cfg, device_id, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from omx_manager import hooks
    cfg['device_id'] = device_id
    cfg['role'] = 'pickup' if device_id in ('omx1', 'omx2') else 'unload'
    clock = Clock()
    device = DeadbandFollower(start=0, deadband=.4, response=.1)
    robot = SimpleNamespace(
        get_joint_observation=device.read,
        get_observation=Mock(side_effect=AssertionError('Home must not read/copy camera frames')),
        _check_motor_health=Mock(),
        send_action=lambda action: device.send({joint: action[joint + '.pos'] for joint in C.JOINTS}),
    )
    monkeypatch.setattr(hooks, 'managed_config', lambda: cfg)
    monkeypatch.setattr(hooks, 'require_home', lambda _: None)
    monkeypatch.setattr(C, 'digest', lambda _: 'unchanged-trained-calibration')
    monkeypatch.setattr(hooks, 'heartbeat', lambda **kwargs: None)
    monkeypatch.setattr(hooks, 'ROOT', tmp_path)
    monkeypatch.setattr(hooks, 'time', SimpleNamespace(monotonic=clock, sleep=clock.sleep, time=lambda: 1000 + clock()))
    for episode in range(20):
        device.q = pose(0 if episode == 0 else 5 + episode % 3)
        started = clock()
        result = hooks.initialize_home(robot)
        assert clock() - started < cfg['home']['move_timeout_s']
        assert result == {joint + '.pos': 0 for joint in C.JOINTS}
        assert max(device.q.values()) <= cfg['home']['arrival_tolerance']
    robot.get_observation.assert_not_called()
    import json
    report = json.loads((tmp_path / 'state' / 'manager' / device_id / 'home_motion.json').read_text())
    assert report['phase'] == 'ARRIVED'
    assert all(row['ok'] for row in report['joints'].values())


def test_real_joint_observation_keeps_range_guards_and_does_not_read_cameras():
    import ast
    from types import SimpleNamespace
    from unittest.mock import Mock
    source = ROOT / 'lerobot_unified/src/lerobot/robots/omx_follower/omx_follower.py'
    tree = ast.parse(source.read_text())
    driver = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'OmxFollower')
    method = copy.deepcopy(next(node for node in driver.body if isinstance(node, ast.FunctionDef) and node.name == 'get_joint_observation'))
    method.decorator_list = []
    method.returns = None
    namespace = {'time': SimpleNamespace(perf_counter=lambda: 0.), 'logger': Mock()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(source), 'exec'), namespace)
    bus = Mock()
    bus.sync_read.side_effect = [pose(100), pose(1)]
    robot = SimpleNamespace(bus=bus, calibration={joint: SimpleNamespace(range_min=0, range_max=4095) for joint in C.JOINTS},
                            _check_motor_health=Mock(), cameras={'wrist': Mock()})
    assert namespace['get_joint_observation'](robot) == {joint + '.pos': 1 for joint in C.JOINTS}
    robot.cameras['wrist'].read_latest.assert_not_called()
    robot._check_motor_health.assert_called_once()
    bus.sync_read.side_effect = [pose(5000)]
    with pytest.raises(RuntimeError, match='outside trained calibration'):
        namespace['get_joint_observation'](robot)


def test_home_diagnostic_failure_has_joint_context_and_preserves_limits(cfg):
    clock = Clock()
    device = DeadbandFollower(deadband=100)
    cfg['home']['move_timeout_s'] = 1
    reports = []
    with pytest.raises(TimeoutError):
        move_home(device, cfg, clock=clock, sleep=clock.sleep, report=reports.append)
    assert reports[-1]['phase'] == 'FAILED'
    assert 'error=10' in reports[-1]['reason']
    assert reports[-1]['joints']['wrist_flex']['count_tolerance'] == 7
    assert reports[-1]['joints']['wrist_flex']['tolerance'] == .8
