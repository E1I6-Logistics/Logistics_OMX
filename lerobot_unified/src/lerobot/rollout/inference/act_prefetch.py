"""Opt-in, bounded ACT chunk prefetch for OMX.

The existing SyncInferenceEngine remains the fallback.  A single worker owns
policy inference/preprocessing; the control thread owns robot action dispatch.
No motor commands are emitted from this module.
"""
from __future__ import annotations

import logging
from contextlib import nullcontext
import os
import threading
import time
from dataclasses import dataclass

import torch

from lerobot.policies.utils import make_robot_action, prepare_observation_for_inference
from .sync import SyncInferenceEngine

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}], got {value}")
    return value


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}], got {value}")
    return value


@dataclass
class _Chunk:
    epoch: int
    actions: list[torch.Tensor]
    observed_at: float


class PrefetchACTInferenceEngine(SyncInferenceEngine):
    """ACT-only optimization, using the SAME pretrained policy and processors.

    Worker generates complete postprocessed chunks ahead of time. Each action
    is applied only by the existing control-thread robot dispatch, which retains
    its abort/stop checks. An empty queue never repeats an old motor command.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        cfg = self._policy.config
        if (getattr(cfg, "type", None) != "act"
                or getattr(cfg, "temporal_ensemble_coeff", None) is not None
                or int(getattr(cfg, "n_action_steps", 0)) < 4
                or not callable(getattr(self._policy, "predict_action_chunk", None))):
            raise ValueError("ACT prefetch needs chunked ACT without temporal ensembling")

        self._steps = int(cfg.n_action_steps)
        self._lookahead = min(self._steps - 1, _int_env("OMX_ACT_PREFETCH_STEPS", 70, 1, 1000))
        self._max_age_s = _float_env("OMX_ACT_PREFETCH_MAX_AGE_S", 4.0, 0.1, 30.0)
        self._stall_s = _float_env("OMX_ACT_PREFETCH_STALL_S", 10.0, 1.0, 120.0)
        self._state_lock = threading.RLock()
        self._model_lock = threading.Lock()
        self._wake = threading.Event()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._epoch = 0
        self._pending: tuple[int, dict, str, float] | None = None
        self._inflight = False
        self._requested_at: float | None = None
        self._ready: _Chunk | None = None
        self._active: _Chunk | None = None
        self._index = 0
        self._failure: BaseException | None = None
        self._activated = False
        self._starved = 0
        logger.info("ACT prefetch configured: steps=%d, lookahead=%d, age<=%.1fs, stall<=%.1fs",
                    self._steps, self._lookahead, self._max_age_s, self._stall_s)

    def start(self) -> None:
        with self._state_lock:
            if self._worker is not None:
                if self._worker.is_alive():
                    return
                raise RuntimeError("ACT prefetch worker unexpectedly stopped")
            self._closed = False
            self._worker = threading.Thread(target=self._worker_loop, name="OMX-ACT-Prefetch", daemon=True)
            self._worker.start()

    def stop(self) -> None:
        with self._state_lock:
            self._closed = True
            self._epoch += 1
            self._pending = None
            self._ready = None
            self._active = None
            self._index = 0
            self._wake.set()
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=self._stall_s)
            if worker.is_alive():
                raise RuntimeError("ACT prefetch worker did not terminate; abort model swap")
        with self._state_lock:
            self._worker = None
        logger.info("ACT prefetch worker stopped; stale actions invalidated")

    def reset(self) -> None:
        # Invalidate before locking inference: a completed old chunk is never visible.
        with self._state_lock:
            self._epoch += 1
            self._pending = None
            self._ready = None
            self._active = None
            self._index = 0
            self._failure = None
            self._requested_at = None
            self._activated = False
            self._wake.set()
        with self._model_lock:
            super().reset()

    def _enqueue(self, obs_frame: dict, task: str) -> None:
        if self._pending is not None or self._inflight or self._ready is not None:
            return
        now = time.monotonic()
        # Frames constructed by build_dataset_frame are unique to this control tick.
        snapshot = dict(obs_frame)
        self._pending = (self._epoch, snapshot, task, now)
        self._requested_at = now
        self._wake.set()

    def _worker_loop(self) -> None:
        while True:
            self._wake.wait(timeout=0.25)
            self._wake.clear()
            with self._state_lock:
                if self._closed:
                    return
                request, self._pending = self._pending, None
                if request is None:
                    continue
                self._inflight = True
            epoch, obs, task, observed_at = request
            try:
                compute_started = time.monotonic()
                autocast_ctx = (
                    torch.autocast(device_type=self._device.type)
                    if self._device.type == "cuda" and getattr(self._policy.config, "use_amp", False)
                    else nullcontext()
                )
                with self._model_lock, torch.inference_mode(), autocast_ctx:
                    frame = prepare_observation_for_inference(obs, self._device, task, self._robot_type)
                    prepared = self._preprocessor(frame)
                    actions = self._policy.predict_action_chunk(prepared)
                    if (not isinstance(actions, torch.Tensor) or actions.ndim != 3
                            or actions.shape[0] != 1 or actions.shape[1] < self._steps):
                        raise RuntimeError("ACT model returned incompatible chunk shape")
                    converted = []
                    for i in range(self._steps):
                        action = self._postprocessor(actions[:, i, :])
                        vec = action.squeeze(0).detach().cpu()
                        mapping = make_robot_action(vec, self._dataset_features)
                        converted.append(torch.tensor([mapping[k] for k in self._ordered_action_keys]))
                if not converted:
                    raise RuntimeError("ACT model produced an empty chunk")
            except BaseException as exc:
                with self._state_lock:
                    if epoch == self._epoch and not self._closed:
                        self._failure = exc
                        logger.exception("[SAFETY] ACT prefetch failed")
                    self._inflight = False
                continue
            with self._state_lock:
                if epoch == self._epoch and not self._closed:
                    self._ready = _Chunk(epoch=epoch, actions=converted, observed_at=observed_at)
                    self._requested_at = None
                self._inflight = False
                logger.info("[ACT-PREFETCH] ready %d actions in %.0f ms (epoch=%d)",
                            len(converted), (time.monotonic() - compute_started) * 1000, epoch)

    def get_action(self, obs_frame: dict | None) -> torch.Tensor | None:
        if obs_frame is None:
            return None
        task, task_changed = self._take_task()
        if task_changed:
            # Stop safely rather than return an old-instruction action. FMS uses
            # reset/model replacement to change tasks between jobs.
            raise RuntimeError("ACT prefetch task changed: reset engine before new actions")
        with self._state_lock:
            if self._closed or self._worker is None or not self._worker.is_alive():
                raise RuntimeError("ACT prefetch worker is not running")
            if self._failure is not None:
                raise RuntimeError("ACT prefetch inference failed") from self._failure
            now = time.monotonic()
            if self._requested_at is not None and now - self._requested_at > self._stall_s:
                raise TimeoutError("ACT prefetch took too long; refusing stale actions")
            if self._active is not None and self._index >= len(self._active.actions):
                self._active = None
                self._index = 0
            if self._active is None and self._ready is not None:
                candidate, self._ready = self._ready, None
                if now - candidate.observed_at <= self._max_age_s:
                    self._active = candidate
                    self._index = 0
                    self._activated = True
                else:
                    logger.warning("[SAFETY] Discarded old ACT prefetch chunk (%.2fs)",
                                   now - candidate.observed_at)
            if self._active is None:
                self._enqueue(obs_frame, task)
                self._starved += 1
                return None
            action = self._active.actions[self._index]
            self._index += 1
            remaining = len(self._active.actions) - self._index
            if remaining <= self._lookahead and remaining > 0:
                self._enqueue(obs_frame, task)
            self._set_dispatched_task(task)
            return action


def create_omx_sync_engine(**kwargs) -> SyncInferenceEngine:
    """Stable model-independent entry point. Unsupported models keep old Sync path."""
    mode = os.environ.get("OMX_ACT_PREFETCH", "0").strip()
    if mode not in {"0", "1"}:
        raise ValueError("OMX_ACT_PREFETCH must be 0 or 1")
    if mode == "1":
        cfg = getattr(kwargs["policy"], "config", None)
        if (getattr(cfg, "type", None) == "act"
                and getattr(cfg, "temporal_ensemble_coeff", None) is None
                and getattr(cfg, "n_action_steps", 0) >= 4
                and callable(getattr(kwargs["policy"], "predict_action_chunk", None))
                and not kwargs["policy"].supports_text_generation()):
            return PrefetchACTInferenceEngine(**kwargs)
        logger.warning("ACT prefetch unsupported by current model; falling back to original sync inference")
    return SyncInferenceEngine(**kwargs)
