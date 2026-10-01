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
        auto_restart = self.config.auto_restart_on_home and bool(home_position)
        waiting_for_inventory = False
        next_inventory_poll = start_time
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
                    obs = robot.get_observation()

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
                                obs = robot.get_observation()
                                now = time.perf_counter()
                            self._total_completed = reloaded_total
                            self._session_completed = 0
                            engine.reset()
                            interpolator.reset()
                            self._cached_obs_processed = None
                            timer.restart()
                            cycle_started_at = now
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
                            elif now - home_since >= self.config.home_hold_s:
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
                                return_detector_armed = False
                                home_since = None
                                engine.resume()
                                continue
                        else:
                            home_since = None
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
            logger.info("Base strategy control loop ended")
            timer.log_run_summary()

    def teardown(self, ctx: RolloutContext) -> None:
        """Disconnect hardware and stop inference."""
        self._teardown_hardware(
            ctx.hardware,
            return_to_initial_position=ctx.runtime.cfg.return_to_initial_position,
        )
        logger.info("Base strategy teardown complete")
