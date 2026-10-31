"""SDK-free regressions for robot finalization and partially opened motor ports."""
import ast
import copy
import gc
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import weakref
from unittest.mock import Mock

import pytest

from omx_manager import config as C
from omx_manager import hardware as H

ROOT = Path(__file__).resolve().parents[1]


def source_method(relative_path, class_name, method_name):
    tree = ast.parse((ROOT / relative_path).read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = copy.deepcopy(next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name))
    method.decorator_list = []
    namespace = {'logger': Mock()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), relative_path, 'exec'), namespace)
    return namespace[method_name]


@pytest.fixture
def motor_setup(monkeypatch, tmp_path):
    cfg = C.load(ROOT / 'params/omx1.yaml')
    cfg['hardware']['calibration_file'] = str(tmp_path / 'omx_follower_arm.json')
    monkeypatch.setattr(H, 'check_files', lambda *args, **kwargs: None)
    monkeypatch.setattr(H, 'port_lock', lambda port: H.Lock(tmp_path / 'motor.lock'))
    state = SimpleNamespace(fail=None, buses=[], robots=[], torque_changes=[])

    class Port:
        def __init__(self):
            self.is_open = False
            self.ser = None
            self.close_calls = 0

        def closePort(self):
            self.close_calls += 1
            # Dynamixel SDK's closePort() accesses ser.close() even if unopened.
            self.ser.close()
            self.is_open = False

    class Bus:
        def __init__(self):
            self.port_handler = Port()
            self.is_calibrated = state.fail != 'calibration'
            self.calibration = {joint: SimpleNamespace(range_min=0, range_max=4095) for joint in C.JOINTS}
            self.disconnect_calls = 0

        @property
        def is_connected(self):
            return self.port_handler.is_open

        def connect(self):
            if state.fail == 'open':
                raise ConnectionError('original open failure: missing serial device')
            self.port_handler.ser = Mock()
            self.port_handler.is_open = True
            if state.fail == 'handshake':
                raise RuntimeError('original handshake failure')

        def read(self, register, joint):
            if not self.is_connected:
                raise RuntimeError('DynamixelMotorsBus is not connected')
            return 1 if register == 'Hardware_Error_Status' and state.fail == 'health' else 0

        def sync_read(self, register, normalize=True):
            if not self.is_connected:
                raise RuntimeError('DynamixelMotorsBus is not connected')
            return {joint: 0.0 if normalize else 100 for joint in C.JOINTS}

        def disconnect(self, disable_torque=True):
            self.disconnect_calls += 1
            if disable_torque:
                state.torque_changes.append(True)
            self.port_handler.closePort()

    class Robot:
        # Exercise the real destructor and OMX disconnect path, rather than a no-op mock.
        __del__ = source_method('lerobot_unified/src/lerobot/robots/robot.py', 'Robot', '__del__')
        disconnect = source_method('lerobot_unified/src/lerobot/robots/omx_follower/omx_follower.py', 'OmxFollower', 'disconnect')

        def __init__(self, config):
            self.config = config
            self.cameras = {}
            self.bus = Bus()
            state.buses.append(self.bus)
            state.robots.append(weakref.ref(self))

        @property
        def is_connected(self):
            return self.bus.is_connected

    def make_config(**kwargs):
        kwargs.setdefault('disable_torque_on_disconnect', False)
        return SimpleNamespace(**kwargs)

    for name, members in (
        ('lerobot.robots.omx_follower.omx_follower', {'OmxFollower': Robot}),
        ('lerobot.robots.omx_follower.config_omx_follower', {'OmxFollowerConfig': make_config}),
    ):
        module = ModuleType(name)
        module.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, module)
    return cfg, state


@pytest.mark.parametrize('device_id', ['omx1', 'omx2', 'omx3', 'omx4'])
def test_owner_survives_entry_and_repeated_observation(motor_setup, device_id):
    cfg, state = motor_setup
    cfg['device_id'] = device_id
    driver = H.FollowerBus(cfg)
    with driver as connected:
        gc.collect()
        assert state.robots[0]() is not None
        assert state.buses[0].is_connected
        for _ in range(20):
            assert set(connected.read()) == set(C.JOINTS)
        assert state.buses[0].disconnect_calls == 0
    gc.collect()
    assert state.robots[0]() is None
    assert not state.buses[0].is_connected
    assert state.buses[0].port_handler.close_calls == 1
    assert not state.torque_changes
    driver.__exit__(None, None, None)
    assert state.buses[0].port_handler.close_calls == 1


@pytest.mark.parametrize('failure,message,close_calls', [
    ('open', 'original open failure', 0),
    ('handshake', 'original handshake failure', 1),
    ('calibration', 'Calibration differs', 1),
    ('health', 'Motor hardware error', 1),
])
def test_entry_failure_preserves_original_and_releases_lock(motor_setup, failure, message, close_calls, tmp_path):
    cfg, state = motor_setup
    state.fail = failure
    with pytest.raises((ConnectionError, RuntimeError), match=message):
        with H.FollowerBus(cfg):
            pytest.fail('failed connection must not enter the body')
    gc.collect()
    assert state.buses[0].port_handler.close_calls == close_calls
    assert not state.torque_changes
    with H.Lock(tmp_path / 'motor.lock'):
        pass


def test_body_failure_survives_cleanup_failure(motor_setup, monkeypatch, tmp_path):
    cfg, state = motor_setup
    warning = Mock()
    monkeypatch.setattr(H.logger, "warning", warning)
    with pytest.raises(ValueError, match='original observation failure'):
        with H.FollowerBus(cfg):
            port = state.buses[0].port_handler
            def fail_close():
                port.is_open = False
                raise OSError('USB disappeared while closing')
            monkeypatch.setattr(port, 'closePort', fail_close)
            raise ValueError('original observation failure')
    warning.assert_called_once()
    assert 'USB disappeared while closing' in str(warning.call_args)
    with H.Lock(tmp_path / 'motor.lock'):
        pass


def test_runtime_cleanup_ignores_never_opened_port(motor_setup):
    cfg, state = motor_setup
    state.fail = 'open'
    module = sys.modules['lerobot.robots.omx_follower.omx_follower']
    robot = module.OmxFollower(SimpleNamespace(disable_torque_on_disconnect=False))
    assert H.cleanup_resources(robot=robot) == []
    assert robot.bus.port_handler.close_calls == 0


def test_cleanup_failure_after_success_is_reported_and_unlocks(motor_setup, monkeypatch, tmp_path):
    cfg, state = motor_setup
    with pytest.raises(OSError, match='close failure'):
        with H.FollowerBus(cfg):
            port = state.buses[0].port_handler
            def fail_close():
                port.is_open = False
                raise OSError('close failure')
            monkeypatch.setattr(port, 'closePort', fail_close)
    with H.Lock(tmp_path / 'motor.lock'):
        pass


def test_keyboard_interrupt_closes_port_without_torque_change(motor_setup):
    cfg, state = motor_setup
    with pytest.raises(KeyboardInterrupt):
        with H.FollowerBus(cfg):
            raise KeyboardInterrupt()
    assert not state.buses[0].is_connected
    assert state.buses[0].port_handler.close_calls == 1
    assert not state.torque_changes


def test_runtime_disconnect_does_not_close_serial_twice(motor_setup):
    cfg, state = motor_setup
    module = sys.modules['lerobot.robots.omx_follower.omx_follower']
    robot = module.OmxFollower(SimpleNamespace(disable_torque_on_disconnect=False))
    robot.bus.connect()
    assert H.cleanup_resources(robot=robot) == []
    assert H.cleanup_resources(robot=robot) == []
    assert robot.bus.port_handler.close_calls == 1
    assert not state.torque_changes
