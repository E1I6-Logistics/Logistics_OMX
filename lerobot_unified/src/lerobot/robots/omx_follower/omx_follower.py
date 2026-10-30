#!/usr/bin/env python

# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import time
from functools import cached_property

from lerobot.cameras import make_cameras_from_configs
from lerobot.lerobot_types import RobotAction, RobotObservation
from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.dynamixel import (
    DriveMode,
    DynamixelMotorsBus,
    OperatingMode,
)
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected

from ..robot import Robot
from ..utils import ensure_safe_goal_position
from .config_omx_follower import OmxFollowerConfig

logger = logging.getLogger(__name__)


class OmxFollower(Robot):
    """
    - [OMX](https://github.com/ROBOTIS-GIT/open_manipulator),
        expansion, developed by Woojin Wie and Junha Cha from [ROBOTIS](https://ai.robotis.com/)
    """

    config_class = OmxFollowerConfig
    name = "omx_follower"

    def __init__(self, config: OmxFollowerConfig):
        super().__init__(config)
        self.config = config
        norm_mode_body = MotorNormMode.DEGREES if config.use_degrees else MotorNormMode.RANGE_M100_100
        self.bus = DynamixelMotorsBus(
            port=self.config.port,
            motors={
                "shoulder_pan": Motor(11, "xl430-w250", norm_mode_body),
                "shoulder_lift": Motor(12, "xl430-w250", norm_mode_body),
                "elbow_flex": Motor(13, "xl430-w250", norm_mode_body),
                "wrist_flex": Motor(14, "xl330-m288", norm_mode_body),
                "wrist_roll": Motor(15, "xl330-m288", norm_mode_body),
                "gripper": Motor(16, "xl330-m288", MotorNormMode.RANGE_0_100),
            },
            calibration=self.calibration,
        )
        self.cameras = make_cameras_from_configs(config.cameras)

    @property
    def _motors_ft(self) -> dict[str, type]:
        return {f"{motor}.pos": float for motor in self.bus.motors}

    @property
    def _cameras_ft(self) -> dict[str, tuple]:
        features: dict[str, tuple] = {}
        for cam in self.cameras:
            cfg = self.config.cameras[cam]
            if getattr(cfg, "use_rgb", True):
                features[cam] = (cfg.height, cfg.width, 3)
            if getattr(cfg, "use_depth", False):
                features[f"{cam}_depth"] = (cfg.height, cfg.width, 1)
        return features

    @cached_property
    def observation_features(self) -> dict[str, type | tuple]:
        return {**self._motors_ft, **self._cameras_ft}

    @cached_property
    def action_features(self) -> dict[str, type]:
        return self._motors_ft

    @property
    def is_connected(self) -> bool:
        return self.bus.is_connected and all(cam.is_connected for cam in self.cameras.values())

    @check_if_already_connected
    def connect(self, calibrate: bool = True) -> None:
        """
        For OMX robots that come pre-calibrated:
        - If default calibration from package doesn't match motors, read from motors and save
        - This allows using pre-calibrated robots without manual calibration
        - If no calibration file exists, use factory default values (homing_offset=0, range_min=0, range_max=4095)
        """

        from omx_manager.hardware import port_lock, cleanup_resources
        self._omx_port_lease = port_lock(self.config.port)
        self._omx_port_lease.__enter__()
        try:
            self.bus.connect()
            # Calibration is a physical identity, never repair it by writing defaults.
            if not self.calibration or not self.is_calibrated:
                raise RuntimeError("Missing/mismatched OMX calibration; automatic calibration writes are forbidden")
            self._check_motor_health()
            for name in self.bus.motors:
                expected = 5 if name == "gripper" else 4
                if self.bus.read("Operating_Mode", name) != expected:
                    raise RuntimeError(f"{name}: existing motor mode must be {expected}; review locally, no automatic mode rewrite")
            for cam in self.cameras.values():
                cam.connect()
        except BaseException:
            cleanup_resources(robot=self)
            raise
        from omx_manager.viewer import attach_robot
        attach_robot(self)
        logger.info(f"{self} connected.")

    @property
    def is_calibrated(self) -> bool:
        return self.bus.is_calibrated

    def calibrate(self) -> None:
        raise RuntimeError("OMX factory calibration writes disabled. Use omx_control capture/home; preserve the trained calibration")

    def _check_motor_health(self, require_torque=False):
        # Poll health at 2Hz; after a long inference it is checked before sending.
        now = time.monotonic()
        if now - getattr(self, "_omx_last_health", -1e9) >= .5:
            torque_on = True
            for name in self.bus.motors:
                status = self.bus.read("Hardware_Error_Status", name)
                if status:
                    raise RuntimeError(f"{name}: hardware fault {status}; no automatic reboot or torque retry")
                torque_on = torque_on and self.bus.read("Torque_Enable", name) == 1
            self._omx_torque_on = torque_on
            self._omx_last_health = now
        if require_torque and not getattr(self, "_omx_torque_on", False):
            raise RuntimeError("Torque OFF; use the explicitly confirmed follower arm operation")

    def configure(self) -> None:
        with self.bus.torque_disabled():
            self.bus.configure_motors()
            # Use 'extended position mode' for all motors except gripper, because in joint mode the servos
            # can't rotate more than 360 degrees (from 0 to 4095) And some mistake can happen while assembling
            # the arm, you could end up with a servo with a position 0 or 4095 at a crucial point
            for motor in self.bus.motors:
                if motor != "gripper":
                    self.bus.write("Operating_Mode", motor, OperatingMode.EXTENDED_POSITION.value)

            # Use 'position control current based' for gripper to be limited by the limit of the current. For
            # the follower gripper, it means it can grasp an object without forcing too much even tho, its
            # goal position is a complete grasp (both gripper fingers are ordered to join and reach a touch).
            # For the leader gripper, it means we can use it as a physical trigger, since we can force with
            # our finger to make it move, and it will move back to its original target position when we
            # release the force.
            self.bus.write("Operating_Mode", "gripper", OperatingMode.CURRENT_POSITION.value)

            # Set better PID values to close the gap between recorded states and actions
            # TODO(rcadene): Implement an automatic procedure to set optimal PID values for each motor
            self.bus.write("Position_P_Gain", "elbow_flex", 1500)
            self.bus.write("Position_I_Gain", "elbow_flex", 0)
            self.bus.write("Position_D_Gain", "elbow_flex", 600)

    def setup_motors(self) -> None:
        for motor in reversed(self.bus.motors):
            input(f"Connect the controller board to the '{motor}' motor only and press enter.")
            self.bus.setup_motor(motor)
            print(f"'{motor}' motor id set to {self.bus.motors[motor].id}")

    @check_if_not_connected
    def get_joint_observation(self) -> RobotObservation:
        # Home approach needs fresh joint feedback, without copying camera images.
        start = time.perf_counter()
        self._check_motor_health()
        raw = self.bus.sync_read("Present_Position", normalize=False)
        for name, value in raw.items():
            cal = self.calibration[name]
            if not cal.range_min <= value <= cal.range_max:
                raise RuntimeError(f"{name}: raw position outside trained calibration range; refusing clamped observation")
        obs_dict = self.bus.sync_read("Present_Position")
        obs_dict = {f"{motor}.pos": val for motor, val in obs_dict.items()}
        dt_ms = (time.perf_counter() - start) * 1e3
        logger.debug(f"{self} read state: {dt_ms:.1f}ms")

        return obs_dict

    @check_if_not_connected
    def get_observation(self) -> RobotObservation:
        obs_dict = self.get_joint_observation()

        # Capture images from cameras
        for cam_key, cam in self.cameras.items():
            if getattr(cam, "use_rgb", True):
                start = time.perf_counter()
                obs_dict[cam_key] = cam.read_latest()
                dt_ms = (time.perf_counter() - start) * 1e3
                logger.debug(f"{self} read {cam_key}: {dt_ms:.1f}ms")

            if getattr(cam, "use_depth", False):
                start = time.perf_counter()
                obs_dict[f"{cam_key}_depth"] = cam.read_latest_depth()
                dt_ms = (time.perf_counter() - start) * 1e3
                logger.debug(f"{self} read {cam_key} depth: {dt_ms:.1f}ms")

        return obs_dict

    @check_if_not_connected
    def send_action(self, action: RobotAction) -> RobotAction:
        """Command arm to move to a target joint configuration.

        The relative action magnitude may be clipped depending on the configuration parameter
        `max_relative_target`. In this case, the action sent differs from original action.
        Thus, this function always returns the action actually sent.

        Args:
            action (RobotAction): The goal positions for the motors.

        Returns:
            RobotAction: The action sent to the motors, potentially clipped.
        """

        from omx_manager.home import joints
        # Reject NaN/missing/out-of-range targets before SDK normalization can clamp them.
        goal_pos = joints(action)

        # Cap goal position when too far away from present position.
        # /!\ Slower fps expected due to reading from the follower.
        if self.config.max_relative_target is not None:
            present_pos = self.bus.sync_read("Present_Position")
            goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in goal_pos.items()}
            goal_pos = ensure_safe_goal_position(goal_present_pos, self.config.max_relative_target)

        # A hardware fault must also block commands after long inference.
        self._check_motor_health(require_torque=True)
        self.bus.sync_write("Goal_Position", goal_pos)
        return {f"{motor}.pos": val for motor, val in goal_pos.items()}

    def disconnect(self):
        from omx_manager.hardware import cleanup_resources
        failures = cleanup_resources(robot=self)
        for failure in failures:
            logger.warning("OMX cleanup: %s", failure)
        logger.info(f"{self} disconnected.")
