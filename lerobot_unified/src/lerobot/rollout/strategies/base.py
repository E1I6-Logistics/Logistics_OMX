# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
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

"""Base rollout strategy: autonomous policy execution with no data recording."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path

import cv2
import numpy as np

from lerobot.utils.cycle_timer import CycleTimer

from ..context import RolloutContext
from ..order_models import OrderModelRouter
from .core import RolloutStrategy, send_next_action

logger = logging.getLogger(__name__)


class BaseStrategy(RolloutStrategy):
    """Autonomous policy rollout with no data recording.

    All actions flow through the ``robot_action_processor`` pipeline
    before reaching the robot.
    """

    def setup(self, ctx: RolloutContext) -> None:
        """Initialise the inference engine."""
        if self.config.camera_auto_reconnect:
            if self.config.camera_reconnect_attempts < 1:
                raise ValueError("strategy.camera_reconnect_attempts must be >= 1")
            if self.config.camera_reconnect_interval_s <= 0:
                raise ValueError("strategy.camera_reconnect_interval_s must be > 0")
        self._basket_mask_polygon: np.ndarray | None = None
        if self.config.stuck_basket_mask_enabled:
            if self.config.stuck_basket_mask_timeout_s <= 0:
                raise ValueError("strategy.stuck_basket_mask_timeout_s must be > 0")
            if self.config.stuck_basket_mask_duration_s <= 0:
                raise ValueError("strategy.stuck_basket_mask_duration_s must be > 0")
            if self.config.stuck_basket_mask_retry_cooldown_s <= 0:
                raise ValueError("strategy.stuck_basket_mask_retry_cooldown_s must be > 0")
            if self.config.stuck_basket_mask_max_retries < 1:
                raise ValueError("strategy.stuck_basket_mask_max_retries must be >= 1")
            if not 0 <= self.config.stuck_basket_mask_value <= 255:
                raise ValueError("strategy.stuck_basket_mask_value must be in [0, 255]")
            try:
                points = [
                    tuple(int(value.strip()) for value in point.split(","))
                    for point in self.config.stuck_basket_mask_polygon.split(";")
                    if point.strip()
                ]
            except ValueError as exc:
                raise ValueError(
                    "strategy.stuck_basket_mask_polygon must look like 'x1,y1;x2,y2;x3,y3;x4,y4'"
                ) from exc
            if len(points) < 3 or any(len(point) != 2 for point in points):
                raise ValueError("strategy.stuck_basket_mask_polygon requires at least three x,y points")
            self._basket_mask_polygon = np.asarray(points, dtype=np.int32)
        if self.config.basket_success_enabled:
            if self._basket_mask_polygon is None:
                raise ValueError("Basket success detection requires stuck_basket_mask_polygon")
            if not 0 <= self.config.basket_success_pixel_threshold <= 255:
                raise ValueError("strategy.basket_success_pixel_threshold must be in [0, 255]")
            if not 0 < self.config.basket_success_changed_ratio <= 1:
                raise ValueError("strategy.basket_success_changed_ratio must be in (0, 1]")
            if self.config.basket_success_min_blob_area <= 0:
                raise ValueError("strategy.basket_success_min_blob_area must be > 0")
            if self.config.basket_success_settle_s < 0:
                raise ValueError("strategy.basket_success_settle_s must be >= 0")
        if self.config.idle_complete_enabled:
            if self.config.auto_restart_on_home:
                raise ValueError("idle_complete_enabled and auto_restart_on_home cannot both be enabled")
            if self.config.idle_complete_hold_s <= 0 or self.config.idle_complete_joint_delta <= 0:
                raise ValueError("Idle completion timing and joint delta must be positive")
            if self.config.home_leave_tolerance <= self.config.home_tolerance:
                raise ValueError("home_leave_tolerance must exceed home_tolerance")
        if self.config.auto_restart_on_home:
            if self.config.home_tolerance <= 0:
                raise ValueError("strategy.home_tolerance must be > 0")
            if self.config.home_leave_tolerance <= self.config.home_tolerance:
                raise ValueError("strategy.home_leave_tolerance must be greater than home_tolerance")
            if self.config.home_hold_s < 0 or self.config.min_cycle_s < 0:
                raise ValueError("strategy.home_hold_s and min_cycle_s must be >= 0")
            if self.config.inventory_capacity < 1:
                raise ValueError("strategy.inventory_capacity must be >= 1")
            if self.config.inventory_poll_interval_s <= 0:
                raise ValueError("strategy.inventory_poll_interval_s must be > 0")
        self._order_router = None
        if self.config.order_models_file:
            if not self.config.auto_restart_on_home or not self.config.keep_alive_when_inventory_empty:
                raise ValueError("Order model routing requires home counting and keep-alive")
            self._order_router = OrderModelRouter(self.config.order_models_file, ctx)
        self._init_engine(ctx)
        self._session_completed = 0
        self._inventory_path = Path(self.config.inventory_state_path).expanduser()
        self._camera_status_path = Path(self.config.camera_status_path).expanduser()
        if self.config.camera_auto_reconnect:
            self._save_camera_status(
                status="running",
                camera="all",
                message="카메라 정상 - ACT 추론 준비",
            )
        self._total_completed = 0
        if self.config.auto_restart_on_home:
            self._total_completed = self._load_inventory()
            if self._order_router is not None:
                # Explicit new orders are required after every process restart.
                self._total_completed = self.config.inventory_capacity
                self._save_inventory()
            elif self.config.inventory_reset_on_start:
                self._total_completed = 0
                self._save_inventory()
                logger.info("Inventory reset: %d items available", self.config.inventory_capacity)
            if self._order_router is not None:
                logger.info(
                    "Order counter: completed=0, remaining=0; waiting for a C/D order (state=%s)",
                    self._inventory_path,
                )
            else:
                logger.info(
                    "Inventory: total=%d/%d, remaining=%d, session=0 (state=%s)",
                    self._total_completed,
                    self.config.inventory_capacity,
                    self._remaining_inventory,
                    self._inventory_path,
                )
        logger.info("Base strategy ready")

    @property
    def _remaining_inventory(self) -> int:
        return max(0, self.config.inventory_capacity - self._total_completed)

    def _load_inventory(self) -> int:
        """Load the persistent total while failing safely on invalid state."""
        if not self._inventory_path.exists():
            return 0
        try:
            state = json.loads(self._inventory_path.read_text(encoding="utf-8"))
            saved_capacity = int(state["capacity"])
            total_completed = int(state["total_completed"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid inventory state file: {self._inventory_path}") from exc
        if saved_capacity != self.config.inventory_capacity:
            raise RuntimeError(
                f"Inventory capacity mismatch: state has {saved_capacity}, "
                f"configuration has {self.config.inventory_capacity}. "
                "Use --strategy.inventory_reset_on_start=true after checking the physical stock."
            )
        if not 0 <= total_completed <= saved_capacity:
            raise RuntimeError(f"Invalid total_completed={total_completed} in {self._inventory_path}")
        return total_completed

    def _save_inventory(self) -> None:
        """Atomically persist inventory so a power loss cannot leave partial JSON."""
        self._inventory_path.parent.mkdir(parents=True, exist_ok=True)
        previous_state: dict[str, object] = {}
        if self._inventory_path.exists():
            try:
                loaded = json.loads(self._inventory_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    previous_state = loaded
            except (OSError, json.JSONDecodeError):
                pass

        state = {
            "capacity": self.config.inventory_capacity,
            "total_completed": self._total_completed,
            "remaining": self._remaining_inventory,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        order_item = previous_state.get("order_item")
        order_quantity = previous_state.get("order_quantity")
        if isinstance(order_item, str) and isinstance(order_quantity, int) and order_quantity >= 0:
            state["order_item"] = order_item
            state["order_quantity"] = order_quantity
            state["order_completed"] = max(0, min(order_quantity, order_quantity - self._remaining_inventory))
            order_started_at = previous_state.get("order_started_at")
            order_started_at_epoch = previous_state.get("order_started_at_epoch")
            if isinstance(order_started_at, str):
                state["order_started_at"] = order_started_at
            if isinstance(order_started_at_epoch, (int, float)):
                state["order_started_at_epoch"] = order_started_at_epoch
        temporary = self._inventory_path.with_suffix(self._inventory_path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self._inventory_path)

    def _count_completed_cycle(self) -> None:
        self._session_completed += 1
        self._total_completed += 1
        self._save_inventory()

        order_item = "unknown"
        order_quantity = self._session_completed + self._remaining_inventory
        try:
            state = json.loads(self._inventory_path.read_text(encoding="utf-8"))
            order_item = str(state.get("order_item", order_item))
            order_quantity = int(state.get("order_quantity", order_quantity))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        order_completed = min(self._session_completed, order_quantity)
        order_remaining = max(0, order_quantity - order_completed)
        logger.info(
            "Order progress | item=%s | completed=%d/%d | remaining=%d",
            order_item,
            order_completed,
            order_quantity,
            order_remaining,
        )

    def _save_camera_status(
        self,
        *,
        status: str,
        camera: str,
        message: str,
        attempt: int = 0,
    ) -> None:
        """Atomically publish camera recovery state for the order console."""
        self._camera_status_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "camera": camera,
            "status": status,
            "attempt": attempt,
            "max_attempts": self.config.camera_reconnect_attempts,
            "message": message,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "updated_at_epoch": time.time(),
        }
        temporary = self._camera_status_path.with_suffix(self._camera_status_path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self._camera_status_path)

    @staticmethod
    def _recoverable_opencv_cameras(robot: object, error: Exception) -> list[tuple[str, object]]:
        """Return OpenCV cameras implicated in an observation failure."""
        cameras = getattr(robot, "cameras", {})
        opencv_cameras = [
            (str(key), camera)
            for key, camera in cameras.items()
            if hasattr(camera, "thread") and hasattr(camera, "connect")
        ]
        unhealthy = [
            (key, camera)
            for key, camera in opencv_cameras
            if getattr(camera, "thread", None) is None
            or not getattr(camera.thread, "is_alive", lambda: False)()
            or not bool(getattr(camera, "is_connected", False))
        ]
        if unhealthy:
            return unhealthy
        # A stale-frame timeout can occur before the reader thread exits. Only
        # treat it as a camera failure when the exception identifies OpenCV.
        return opencv_cameras if "OpenCVCamera" in str(error) else []

    def _get_observation_with_camera_recovery(
        self,
        robot: object,
        engine: object,
    ) -> tuple[dict, bool]:
        """Read one observation, reconnecting failed OpenCV cameras when enabled."""
        try:
            return robot.get_observation(), False
        except (RuntimeError, TimeoutError) as error:
            if not self.config.camera_auto_reconnect:
                raise
            cameras = self._recoverable_opencv_cameras(robot, error)
            if not cameras:
                raise

            camera_names = ",".join(key for key, _ in cameras)
            logger.warning(
                "Camera read failed (%s). Pausing actions and reconnecting: %s",
                error,
                camera_names,
            )
            engine.pause()
            last_error: Exception = error

            for attempt in range(1, self.config.camera_reconnect_attempts + 1):
                self._save_camera_status(
                    status="reconnecting",
                    camera=camera_names,
                    attempt=attempt,
                    message=(
                        f"{camera_names} 화면 끊김 - OMX 일시정지, "
                        f"재연결 중 ({attempt}/{self.config.camera_reconnect_attempts})"
                    ),
                )
                for _, camera in cameras:
                    try:
                        camera.disconnect()
                    except Exception:
                        logger.debug("Camera cleanup before reconnect failed", exc_info=True)

                time.sleep(self.config.camera_reconnect_interval_s)

                try:
                    for _, camera in cameras:
                        camera.connect()
                    observation = robot.get_observation()
                except Exception as reconnect_error:
                    last_error = reconnect_error
                    logger.warning(
                        "Camera reconnect attempt %d/%d failed: %s",
                        attempt,
                        self.config.camera_reconnect_attempts,
                        reconnect_error,
                    )
                    continue

                # Drop actions and processor state produced before the outage.
                self.reset_control_state()
                engine.resume()
                self._save_camera_status(
                    status="recovered",
                    camera=camera_names,
                    attempt=attempt,
                    message=f"{camera_names} 재연결 완료 - 새 프레임으로 ACT 추론 재시작",
                )
                logger.info("Camera reconnected after %d attempt(s): %s", attempt, camera_names)
                return observation, True

            self._save_camera_status(
                status="failed",
                camera=camera_names,
                attempt=self.config.camera_reconnect_attempts,
                message=f"{camera_names} 재연결 실패 - USB 연결을 확인하세요",
            )
            raise RuntimeError(
                f"Camera {camera_names} could not reconnect after "
                f"{self.config.camera_reconnect_attempts} attempts"
            ) from last_error

    def _mask_basket_for_one_query(self, observation: dict) -> dict:
        """Return a shallow observation copy with one camera's basket ROI filled."""
        camera_key = self.config.stuck_basket_mask_camera
        if camera_key not in observation:
            raise KeyError(
                f"Basket-mask camera '{camera_key}' is missing; available keys={sorted(observation)}"
            )
        image = observation[camera_key]
        if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
            raise TypeError(
                f"Basket-mask camera '{camera_key}' must be a numpy image, got {type(image).__name__}"
            )

        polygon = self._basket_mask_polygon
        if polygon is None:
            raise RuntimeError("Basket-mask polygon was not initialised")
        height, width = image.shape[:2]
        if (
            np.any(polygon[:, 0] < 0)
            or np.any(polygon[:, 0] >= width)
            or np.any(polygon[:, 1] < 0)
            or np.any(polygon[:, 1] >= height)
        ):
            raise ValueError(f"Basket-mask polygon is outside the {width}x{height} camera frame")

        # Vectorised ray casting keeps the rollout strategy independent from
        # OpenCV and works for either clockwise or counter-clockwise points.
        yy, xx = np.mgrid[:height, :width]
        inside = np.zeros((height, width), dtype=bool)
        previous = polygon[-1]
        for current in polygon:
            crosses_scanline = (current[1] > yy) != (previous[1] > yy)
            edge_x = (previous[0] - current[0]) * (yy - current[1]) / (
                previous[1] - current[1] + np.finfo(float).eps
            ) + current[0]
            inside ^= crosses_scanline & (xx < edge_x)
            previous = current

        masked = image.copy()
        masked[inside] = self.config.stuck_basket_mask_value
        result = observation.copy()
        result[camera_key] = masked
        return result

    def _evaluate_basket_change(self, before: np.ndarray, after: np.ndarray) -> tuple[bool, float, float]:
        """Compare only the configured basket polygon in two unmasked RGB frames."""
        if before.shape != after.shape or before.ndim != 3:
            raise ValueError(f"Basket frames must have matching HxWxC shapes, got {before.shape} and {after.shape}")
        polygon = self._basket_mask_polygon
        if polygon is None:
            raise RuntimeError("Basket polygon is not configured")
        height, width = before.shape[:2]
        if np.any(polygon[:, 0] < 0) or np.any(polygon[:, 0] >= width):
            raise ValueError(f"Basket polygon x coordinates exceed image width {width}")
        if np.any(polygon[:, 1] < 0) or np.any(polygon[:, 1] >= height):
            raise ValueError(f"Basket polygon y coordinates exceed image height {height}")

        roi_mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(roi_mask, [polygon], 255)
        before_blur = cv2.GaussianBlur(before, (5, 5), 0)
        after_blur = cv2.GaussianBlur(after, (5, 5), 0)
        before_lab = cv2.cvtColor(before_blur, cv2.COLOR_RGB2LAB)
        after_lab = cv2.cvtColor(after_blur, cv2.COLOR_RGB2LAB)
        delta = cv2.absdiff(before_lab, after_lab)
        changed = (np.max(delta, axis=2) >= self.config.basket_success_pixel_threshold).astype(np.uint8) * 255
        changed = cv2.bitwise_and(changed, roi_mask)
        kernel = np.ones((3, 3), dtype=np.uint8)
        changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, kernel)
        changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, kernel, iterations=2)

        roi_pixels = int(np.count_nonzero(roi_mask))
        changed_ratio = float(np.count_nonzero(changed)) / max(1, roi_pixels)
        contours, _ = cv2.findContours(changed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        largest_blob = max((cv2.contourArea(contour) for contour in contours), default=0.0)
        success = (
            changed_ratio >= self.config.basket_success_changed_ratio
            and largest_blob >= self.config.basket_success_min_blob_area
        )
        return success, changed_ratio, float(largest_blob)

    @staticmethod
    def _max_home_error(observation: Mapping[str, object], home: Mapping[str, object]) -> float | None:
        """Return the largest absolute joint-position error from the startup pose."""
        errors: list[float] = []
        for key, target in home.items():
            if key not in observation:
                continue
            try:
                errors.append(abs(float(observation[key]) - float(target)))
            except (TypeError, ValueError):
                logger.debug("Ignoring non-scalar home-position value for %s", key)
        return max(errors) if errors else None

    def run(self, ctx: RolloutContext) -> None:
        """Run the autonomous control loop until shutdown or duration expires."""
        engine = self._engine
        cfg = ctx.runtime.cfg
        robot = ctx.hardware.robot_wrapper
        interpolator = self._interpolator

        timer = CycleTimer(
            cfg.fps,
            interpolator.multiplier,
            records_data=False,
            report=ctx.runtime.cadence_report,
        )

        start_time = time.perf_counter()
        cycle_started_at = start_time
        return_detector_armed = False
        home_since: float | None = None
        home_position = ctx.hardware.initial_position or {}
        idle_enabled = self.config.idle_complete_enabled
        if idle_enabled and not home_position:
            raise RuntimeError("Idle completion requires the captured initial joint positions")
        idle_departed = False
        idle_home_since: float | None = None
        idle_previous: dict[str, float] | None = None
        idle_moved_at: float | None = None
        idle_completion = False
        auto_restart = self.config.auto_restart_on_home and bool(home_position)
        waiting_for_inventory = False
        next_inventory_poll = start_time
        basket_mask_retries = 0
        next_basket_mask_retry = start_time + self.config.stuck_basket_mask_timeout_s
        basket_mask_active_until = 0.0
        basket_reference: np.ndarray | None = None
        if self.config.auto_restart_on_home and not home_position:
            logger.warning("Auto restart requested, but no initial joint position was captured; disabling it")
        engine.resume()
        logger.info("Base strategy control loop started (auto_restart_on_home=%s)", auto_restart)

        if auto_restart and self._remaining_inventory == 0:
            if self.config.keep_alive_when_inventory_empty:
                waiting_for_inventory = True
                logger.warning(
                    "Inventory empty: %d/%d completed. OMX is waiting at home; connections remain active.",
                    self._total_completed,
                    self.config.inventory_capacity,
                )
            else:
                logger.warning(
                    "Inventory empty: %d/%d completed. OMX will not move.",
                    self._total_completed,
                    self.config.inventory_capacity,
                )
                return

        try:
            while not ctx.runtime.shutdown_event.is_set():
                timer.tick(new_cycle=interpolator.needs_new_action())

                if cfg.duration > 0 and (time.perf_counter() - start_time) >= cfg.duration:
                    logger.info("Duration limit reached (%.0fs)", cfg.duration)
                    break

                with timer.section("observe"):
                    obs, camera_recovered = self._get_observation_with_camera_recovery(robot, engine)
                if camera_recovered:
                    timer.restart()

                # OMX3/4 only: use the existing joint observation, no added camera/model work.
                # Never report an initially stationary robot as a completed unloading job.
                if idle_enabled:
                    now = time.perf_counter()
                    joints = {}
                    for key in home_position:
                        if key in obs:
                            try:
                                joints[key] = float(obs[key])
                            except (ValueError, TypeError):
                                pass
                    if len(joints) != len(home_position):
                        idle_home_since = None
                    else:
                        home_error = self._max_home_error(obs, home_position)
                        if home_error is not None and home_error >= self.config.home_leave_tolerance:
                            idle_departed = True
                        if idle_previous is None:
                            idle_moved_at = now
                        else:
                            step_delta = max(abs(joints[k] - idle_previous[k]) for k in joints)
                            if step_delta >= self.config.idle_complete_joint_delta:
                                idle_moved_at = now
                        idle_previous = joints
                        if idle_departed and home_error is not None and home_error <= self.config.home_tolerance:
                            if idle_home_since is None:
                                idle_home_since = now
                            if (idle_moved_at is not None
                                and now - max(idle_home_since, idle_moved_at) >= self.config.idle_complete_hold_s):
                                logger.warning("OMX idle completion: returned home and held still for %.1fs",
                                               self.config.idle_complete_hold_s)
                                idle_completion = True
                                break
                        else:
                            idle_home_since = None

                if waiting_for_inventory:
                    now = time.perf_counter()
                    if now >= next_inventory_poll:
                        next_inventory_poll = now + self.config.inventory_poll_interval_s
                        reloaded_total = self._load_inventory()
                        if reloaded_total < self.config.inventory_capacity:
                            if self._order_router is not None:
                                home_error = self._max_home_error(obs, home_position)
                                if home_error is None or home_error > self.config.home_tolerance:
                                    logger.warning("Order pending: robot must be at the initial pose")
                                    timer.wait()
                                    continue
                                try:
                                    state = json.loads(self._inventory_path.read_text())
                                    self._order_router.select(str(state.get("order_item", "")), ctx)
                                except Exception:
                                    logger.exception("Model selection failed; staying idle. Check model and order.")
                                    timer.wait()
                                    continue
                                engine = ctx.policy.inference
                                self._engine = engine
                                # Loading may take seconds; do not act on pre-load camera frames.
                                obs, camera_recovered = self._get_observation_with_camera_recovery(robot, engine)
                                if camera_recovered:
                                    timer.restart()
                                now = time.perf_counter()
                            self._total_completed = reloaded_total
                            self._session_completed = 0
                            engine.reset()
                            interpolator.reset()
                            self._cached_obs_processed = None
                            timer.restart()
                            cycle_started_at = now
                            basket_mask_retries = 0
                            next_basket_mask_retry = now + self.config.stuck_basket_mask_timeout_s
                            basket_mask_active_until = 0.0
                            basket_reference = None
                            return_detector_armed = False
                            home_since = None
                            waiting_for_inventory = False
                            engine.resume()
                            logger.info(
                                "New inventory/order detected: remaining=%d. Resuming policy.",
                                self._remaining_inventory,
                            )
                    if waiting_for_inventory:
                        timer.wait()
                        continue

                if auto_restart:
                    now = time.perf_counter()
                    if self.config.basket_success_enabled and basket_reference is None:
                        basket_image = obs.get(self.config.stuck_basket_mask_camera)
                        if not isinstance(basket_image, np.ndarray):
                            raise RuntimeError(
                                f"Basket camera '{self.config.stuck_basket_mask_camera}' did not provide an image"
                            )
                        basket_reference = basket_image.copy()
                        logger.info("Captured basket reference frame for success detection")
                    home_error = self._max_home_error(obs, home_position)
                    if home_error is not None:
                        if not return_detector_armed:
                            if (
                                now - cycle_started_at >= self.config.min_cycle_s
                                and home_error >= self.config.home_leave_tolerance
                            ):
                                return_detector_armed = True
                                logger.info("Cycle return detector armed (home error %.2f)", home_error)
                        elif home_error <= self.config.home_tolerance:
                            if home_since is None:
                                home_since = now
                            elif now - home_since >= max(
                                self.config.home_hold_s,
                                self.config.basket_success_settle_s if self.config.basket_success_enabled else 0.0,
                            ):
                                if self.config.basket_success_enabled:
                                    basket_image = obs.get(self.config.stuck_basket_mask_camera)
                                    if not isinstance(basket_image, np.ndarray) or basket_reference is None:
                                        raise RuntimeError("Basket success detector has no valid reference/current frame")
                                    success, changed_ratio, largest_blob = self._evaluate_basket_change(
                                        basket_reference, basket_image
                                    )
                                    verdict = "성공" if success else "실패"
                                    logger.info(
                                        "Basket transfer verdict=%s changed_ratio=%.4f largest_blob=%.1f",
                                        verdict,
                                        changed_ratio,
                                        largest_blob,
                                    )
                                    self._save_camera_status(
                                        status="basket_success" if success else "basket_failure",
                                        camera=self.config.stuck_basket_mask_camera,
                                        message=(
                                            f"바구니 이동 {verdict} - 변화율 {changed_ratio * 100:.2f}%, "
                                            f"최대 변화영역 {largest_blob:.0f}px"
                                        ),
                                    )
                                    basket_reference = basket_image.copy()
                                    if not success:
                                        engine.reset()
                                        interpolator.reset()
                                        self._cached_obs_processed = None
                                        timer.restart()
                                        cycle_started_at = now
                                        basket_mask_retries = 0
                                        next_basket_mask_retry = now + self.config.stuck_basket_mask_timeout_s
                                        basket_mask_active_until = 0.0
                                        return_detector_armed = False
                                        home_since = None
                                        engine.resume()
                                        logger.warning("Basket transfer failed; retrying the same order item")
                                        continue
                                self._count_completed_cycle()
                                if self._remaining_inventory == 0:
                                    logger.warning(
                                        "Inventory empty: %d/%d completed. OMX stopping at home.",
                                        self._total_completed,
                                        self.config.inventory_capacity,
                                    )
                                    if self.config.keep_alive_when_inventory_empty:
                                        engine.reset()
                                        interpolator.reset()
                                        self._cached_obs_processed = None
                                        return_detector_armed = False
                                        home_since = None
                                        waiting_for_inventory = True
                                        next_inventory_poll = now + self.config.inventory_poll_interval_s
                                        logger.info(
                                            "Waiting for a new inventory/order update; connections remain active"
                                        )
                                        continue
                                    break

                                logger.info("Resetting policy for the next item")
                                engine.reset()
                                interpolator.reset()
                                self._cached_obs_processed = None
                                timer.restart()
                                cycle_started_at = now
                                basket_mask_retries = 0
                                next_basket_mask_retry = now + self.config.stuck_basket_mask_timeout_s
                                basket_mask_active_until = 0.0
                                return_detector_armed = False
                                home_since = None
                                engine.resume()
                                continue
                        else:
                            home_since = None

                    if (
                        self.config.stuck_basket_mask_enabled
                        and not return_detector_armed
                        and now - cycle_started_at >= self.config.stuck_basket_mask_timeout_s
                        and basket_mask_retries < self.config.stuck_basket_mask_max_retries
                        and now >= next_basket_mask_retry
                    ):
                        basket_mask_retries += 1
                        next_basket_mask_retry = now + self.config.stuck_basket_mask_retry_cooldown_s
                        logger.warning(
                            "Robot stayed at home for %.1fs; applying basket mask for %.1fs (%d/%d)",
                            now - cycle_started_at,
                            self.config.stuck_basket_mask_duration_s,
                            basket_mask_retries,
                            self.config.stuck_basket_mask_max_retries,
                        )
                        self.reset_control_state()
                        timer.restart()
                        engine.resume()
                        basket_mask_active_until = now + self.config.stuck_basket_mask_duration_s
                        self._save_camera_status(
                            status="masking",
                            camera=self.config.stuck_basket_mask_camera,
                            attempt=basket_mask_retries,
                            message=(
                                f"바구니 영역 흰색 마스킹 시작 - "
                                f"{self.config.stuck_basket_mask_duration_s:.1f}초 유지 "
                                f"({basket_mask_retries}/{self.config.stuck_basket_mask_max_retries})"
                            ),
                        )
                    if now < basket_mask_active_until:
                        obs = self._mask_basket_for_one_query(obs)
                    elif basket_mask_active_until > 0:
                        basket_mask_active_until = 0.0
                        self._save_camera_status(
                            status="mask_cleared",
                            camera=self.config.stuck_basket_mask_camera,
                            message="바구니 영역 마스킹 해제 - 원본 카메라 영상으로 복귀",
                        )
                with timer.section("process_obs"):
                    obs_processed = self._process_observation_and_notify(ctx.processors, obs)

                if self._handle_warmup(cfg.use_torch_compile, timer):
                    continue

                action_dict = send_next_action(obs_processed, obs, ctx, interpolator, timer)
                with timer.section("telemetry"):
                    self._log_telemetry(obs_processed, action_dict, ctx.runtime)

                # Service the text-query channel (/vqa answers, /autosteer turns) at
                # the end of the tick; no-op when nothing is queued.
                with timer.section("query"):
                    engine.pump_query(obs_processed)

                timer.wait()
        finally:
            if idle_enabled and idle_completion:
                marker = os.environ.get("E_IDLE_COMPLETION_FILE", "")
                if not marker:
                    raise RuntimeError("E_IDLE_COMPLETION_FILE must be set for idle completion")
                path = Path(marker)
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_name(path.name + ".tmp")
                temp.write_text(json.dumps({"reason": "home_stillness_after_departure",
                                            "hold_s": self.config.idle_complete_hold_s}) + "\n")
                os.replace(temp, path)
            logger.info("Base strategy control loop ended")
            timer.log_run_summary()

    def teardown(self, ctx: RolloutContext) -> None:
        """Disconnect hardware and stop inference."""
        self._teardown_hardware(
            ctx.hardware,
            return_to_initial_position=ctx.runtime.cfg.return_to_initial_position,
        )
        logger.info("Base strategy teardown complete")
