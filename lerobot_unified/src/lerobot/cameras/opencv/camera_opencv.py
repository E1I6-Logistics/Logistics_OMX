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

"""
Provides the OpenCVCamera class for capturing frames from cameras using OpenCV.
"""

import logging
from lerobot.utils.runtime_metrics import camera_event
import math
import os
import platform
import time
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from numpy.typing import NDArray  # type: ignore  # TODO: add type stubs for numpy.typing

# Fix MSMF hardware transform compatibility for Windows before importing cv2
if platform.system() == "Windows" and "OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS" not in os.environ:
    os.environ["OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS"] = "0"
import cv2  # type: ignore  # TODO: add type stubs for OpenCV

from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected
from lerobot.utils.errors import DeviceNotConnectedError

from ..camera import Camera
from ..utils import get_cv2_rotation
from .configuration_opencv import ColorMode, OpenCVCameraConfig

# NOTE(Steven): The maximum opencv device index depends on your operating system. For instance,
# if you have 3 cameras, they should be associated to index 0, 1, and 2. This is the case
# on MacOS. However, on Ubuntu, the indices are different like 6, 16, 23.
# When you change the USB port or reboot the computer, the operating system might
# treat the same cameras as new devices. Thus we select a higher bound to search indices.
MAX_OPENCV_INDEX = 60

logger = logging.getLogger(__name__)


class OpenCVCamera(Camera):
    """
    Manages camera interactions using OpenCV for efficient frame recording.

    This class provides a high-level interface to connect to, configure, and read
    frames from cameras compatible with OpenCV's VideoCapture. It supports both
    synchronous and asynchronous frame reading.

    The camera can capture hardware frames at one FPS rate while publishing processed
    frames at a lower rate using `processing_fps`.

    Example:
        ```python
        from lerobot.cameras.opencv import OpenCVCamera
        from lerobot.cameras.configuration_opencv import OpenCVCameraConfig

        # Hardware capture at 30 FPS, publish/process at 15 FPS
        config = OpenCVCameraConfig(
            index_or_path="/dev/video9",
            fps=30,
            width=640,
            height=480,
            processing_fps=15,
        )

        camera = OpenCVCamera(config)
        camera.connect()

        color_image = camera.read_latest()

        camera.disconnect()
        ```
    """

    def __init__(self, config: OpenCVCameraConfig):
        """
        Initializes the OpenCVCamera instance.

        Args:
            config: The configuration settings for the camera.
        """
        super().__init__(config)

        self.config = config
        self.index_or_path = config.index_or_path

        # Hardware capture FPS.
        self.fps = config.fps

        # Frame processing/publishing FPS.
        # Example:
        #   fps=30
        #   processing_fps=15
        # means the camera hardware is still read at 30 FPS,
        # but latest_frame is updated at approximately 15 FPS.
        self.processing_fps = config.processing_fps

        self.color_mode = config.color_mode
        self.warmup_s = config.warmup_s

        self.videocapture: cv2.VideoCapture | None = None

        self.thread: Thread | None = None
        self.stop_event: Event | None = None
        self.frame_lock: Lock = Lock()
        # Serialize capture reads with managed inspection sensor controls.
        self.control_lock: Lock = Lock()
        self.latest_frame: NDArray[Any] | None = None
        self.latest_timestamp: float | None = None
        self.new_frame_event: Event = Event()

        self.rotation: int | None = get_cv2_rotation(config.rotation)
        self.backend: int = config.backend

        self.capture_width: int | None = None
        self.capture_height: int | None = None
        self._reset_connection_settings()

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.index_or_path})"

    def _reset_connection_settings(self) -> None:
        """Restore settings that may have been auto-detected during a failed connection."""
        self.fps = self.config.fps
        self.width = self.config.width
        self.height = self.config.height
        self.capture_width, self.capture_height = self.width, self.height

        if self.rotation in [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE]:
            self.capture_width, self.capture_height = self.height, self.width

    @property
    def is_connected(self) -> bool:
        """Checks if the camera is currently connected and opened."""
        return isinstance(self.videocapture, cv2.VideoCapture) and self.videocapture.isOpened()

    @check_if_already_connected
    def connect(self, warmup: bool = True) -> None:
        """
        Connects to the OpenCV camera specified in the configuration.

        Initializes the OpenCV VideoCapture object, sets desired camera properties
        (FPS, resolution), starts the background reading thread and performs initial checks.

        Args:
            warmup (bool): If True, waits at connect() time until at least one valid frame
                           has been captured by the background thread. Defaults to True.
        """

        # Use 1 thread for OpenCV operations to avoid potential conflicts or
        # blocking in multi-threaded applications, especially during data collection.
        cv2.setNumThreads(1)

        self.videocapture = cv2.VideoCapture(self.index_or_path, self.backend)

        if not self.videocapture.isOpened():
            self.videocapture.release()
            self.videocapture = None
            raise ConnectionError(
                f"Failed to open {self}.Run `lerobot-find-cameras opencv` to find available cameras."
            )

        try:
            self._configure_capture_settings()
            # Best effort: supported V4L2 devices should not accumulate old frames.
            try:self.videocapture.set(cv2.CAP_PROP_BUFFERSIZE,1)
            except cv2.error:logger.debug('Capture backend does not support buffer-size tuning')
            self._start_read_thread()

            if warmup and self.warmup_s > 0:
                start_time = time.time()

                while time.time() - start_time < self.warmup_s:
                    self.async_read(timeout_ms=self.warmup_s * 1000)
                    time.sleep(0.1)

                with self.frame_lock:
                    if self.latest_frame is None:
                        raise ConnectionError(f"{self} failed to capture frames during warmup.")

        except BaseException:
            try:
                self._cleanup_resources()
            except Exception:
                logger.exception(f"Failed to fully clean up {self} after connect() failed.")

            self._reset_connection_settings()
            raise

        logger.info(
            f"{self} connected "
            f"(hardware_fps={self.fps}, processing_fps={self.processing_fps})."
        )

    @check_if_not_connected
    def _configure_capture_settings(self) -> None:
        """
        Applies the specified FOURCC, FPS, width, and height settings to the connected camera.
        """

        if self.videocapture is None:
            raise DeviceNotConnectedError(f"{self} videocapture is not initialized")

        set_fourcc_after_size_and_fps = platform.system() == "Windows"

        if self.config.fourcc is not None and not set_fourcc_after_size_and_fps:
            self._validate_fourcc()

        default_width = int(round(self.videocapture.get(cv2.CAP_PROP_FRAME_WIDTH)))
        default_height = int(round(self.videocapture.get(cv2.CAP_PROP_FRAME_HEIGHT)))

        if self.width is None or self.height is None:
            self.width, self.height = default_width, default_height
            self.capture_width, self.capture_height = default_width, default_height

            if self.rotation in [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE]:
                self.width, self.height = default_height, default_width
                self.capture_width, self.capture_height = default_width, default_height
        else:
            self._validate_width_and_height()

        if self.fps is None:
            self.fps = self.videocapture.get(cv2.CAP_PROP_FPS)
        else:
            self._validate_fps()

        if self.config.fourcc is not None and set_fourcc_after_size_and_fps:
            self._validate_fourcc()

    def _validate_fps(self) -> None:
        """Validates and sets the camera's hardware capture FPS."""

        if self.videocapture is None:
            raise DeviceNotConnectedError(f"{self} videocapture is not initialized")

        if self.fps is None:
            raise ValueError(f"{self} FPS is not set")

        success = self.videocapture.set(cv2.CAP_PROP_FPS, float(self.fps))
        actual_fps = self.videocapture.get(cv2.CAP_PROP_FPS)

        if not success or not math.isclose(self.fps, actual_fps, rel_tol=1e-3):
            raise RuntimeError(f"{self} failed to set fps={self.fps} ({actual_fps=}).")

    def _validate_fourcc(self) -> None:
        """Validates and sets the camera's FOURCC code."""

        fourcc_code = cv2.VideoWriter_fourcc(*self.config.fourcc)

        if self.videocapture is None:
            raise DeviceNotConnectedError(f"{self} videocapture is not initialized")

        success = self.videocapture.set(cv2.CAP_PROP_FOURCC, fourcc_code)
        actual_fourcc_code = self.videocapture.get(cv2.CAP_PROP_FOURCC)

        actual_fourcc_code_int = int(actual_fourcc_code)
        actual_fourcc = "".join(
            [chr((actual_fourcc_code_int >> 8 * i) & 0xFF) for i in range(4)]
        )

        if not success or actual_fourcc != self.config.fourcc:
            logger.warning(
                f"{self} failed to set fourcc={self.config.fourcc} "
                f"(actual={actual_fourcc}, success={success}). "
                f"Continuing with default format."
            )

    def _validate_width_and_height(self) -> None:
        """Validates and sets the camera's frame capture width and height."""

        if self.videocapture is None:
            raise DeviceNotConnectedError(f"{self} videocapture is not initialized")

        if self.capture_width is None or self.capture_height is None:
            raise ValueError(f"{self} capture_width or capture_height is not set")

        width_success = self.videocapture.set(
            cv2.CAP_PROP_FRAME_WIDTH, float(self.capture_width)
        )
        height_success = self.videocapture.set(
            cv2.CAP_PROP_FRAME_HEIGHT, float(self.capture_height)
        )

        actual_width = int(round(self.videocapture.get(cv2.CAP_PROP_FRAME_WIDTH)))

        if not width_success or self.capture_width != actual_width:
            raise RuntimeError(
                f"{self} failed to set capture_width={self.capture_width} "
                f"({actual_width=}, {width_success=})."
            )

        actual_height = int(round(self.videocapture.get(cv2.CAP_PROP_FRAME_HEIGHT)))

        if not height_success or self.capture_height != actual_height:
            raise RuntimeError(
                f"{self} failed to set capture_height={self.capture_height} "
                f"({actual_height=}, {height_success=})."
            )

    @staticmethod
    def find_cameras() -> list[dict[str, Any]]:
        """
        Detects available OpenCV cameras connected to the system.
        """

        found_cameras_info = []

        targets_to_scan: list[str | int]

        if platform.system() == "Linux":
            possible_paths = sorted(Path("/dev").glob("video*"), key=lambda p: p.name)
            targets_to_scan = [str(p) for p in possible_paths]
        else:
            targets_to_scan = [int(i) for i in range(MAX_OPENCV_INDEX)]

        for target in targets_to_scan:
            camera = cv2.VideoCapture(target)

            try:
                if camera.isOpened():
                    default_width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
                    default_height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    default_fps = camera.get(cv2.CAP_PROP_FPS)
                    default_format = camera.get(cv2.CAP_PROP_FORMAT)

                    default_fourcc_code = camera.get(cv2.CAP_PROP_FOURCC)
                    default_fourcc_code_int = int(default_fourcc_code)

                    default_fourcc = "".join(
                        [chr((default_fourcc_code_int >> 8 * i) & 0xFF) for i in range(4)]
                    )

                    camera_info = {
                        "name": f"OpenCV Camera @ {target}",
                        "type": "OpenCV",
                        "id": target,
                        "backend_api": camera.getBackendName(),
                        "default_stream_profile": {
                            "format": default_format,
                            "fourcc": default_fourcc,
                            "width": default_width,
                            "height": default_height,
                            "fps": default_fps,
                        },
                    }

                    found_cameras_info.append(camera_info)

            finally:
                camera.release()

        return found_cameras_info

    def _read_from_hardware(self) -> NDArray[Any]:
        if self.videocapture is None:
            raise DeviceNotConnectedError(f"{self} videocapture is not initialized")

        with self.control_lock:
            ret, frame = self.videocapture.read()

        if not ret:
            raise RuntimeError(f"{self} read failed (status={ret}).")

        return frame

    @check_if_not_connected
    def read(self, color_mode: ColorMode | None = None) -> NDArray[Any]:
        """
        Reads a single frame synchronously from the camera.
        """

        start_time = time.perf_counter()

        if color_mode is not None:
            logger.warning(
                f"{self} read() color_mode parameter is deprecated "
                f"and will be removed in future versions."
            )

        if self.thread is None or not self.thread.is_alive():
            raise RuntimeError(f"{self} read thread is not running.")

        self.new_frame_event.clear()
        frame = self.async_read(timeout_ms=10000)

        read_duration_ms = (time.perf_counter() - start_time) * 1e3
        logger.debug(f"{self} read took: {read_duration_ms:.1f}ms")

        return frame

    def _postprocess_image(self, image: NDArray[Any]) -> NDArray[Any]:
        """
        Applies color conversion, dimension validation, and rotation to a raw frame.
        """

        if self.color_mode not in (ColorMode.RGB, ColorMode.BGR):
            raise ValueError(
                f"Invalid color mode '{self.color_mode}'. "
                f"Expected {ColorMode.RGB} or {ColorMode.BGR}."
            )

        h, w, c = image.shape

        if h != self.capture_height or w != self.capture_width:
            raise RuntimeError(
                f"{self} frame width={w} or height={h} do not match "
                f"configured width={self.capture_width} or height={self.capture_height}."
            )

        if c != 3:
            raise RuntimeError(
                f"{self} frame channels={c} do not match expected 3 channels (RGB/BGR)."
            )

        processed_image = image

        if self.color_mode == ColorMode.RGB:
            processed_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        if self.rotation in [
            cv2.ROTATE_90_CLOCKWISE,
            cv2.ROTATE_90_COUNTERCLOCKWISE,
            cv2.ROTATE_180,
        ]:
            processed_image = cv2.rotate(processed_image, self.rotation)

        return processed_image

    def _read_loop(self) -> None:
        """
        Internal loop run by the background thread for asynchronous reading.

        Hardware frames are continuously captured at `self.fps`.

        If `processing_fps` is configured below the hardware FPS, only frames
        matching the processing interval are postprocessed and published to
        `latest_frame`.

        Example:
            fps=30
            processing_fps=15

        means:
            Hardware capture: approximately 30 FPS
            Published/processed frames: approximately 15 FPS
        """

        stop_event = self.stop_event

        if stop_event is None:
            raise RuntimeError(
                f"{self}: stop_event is not initialized before starting read loop."
            )

        failure_count = 0

        # Determine the interval at which frames should be published.
        if self.processing_fps is not None:
            processing_interval = 1.0 / self.processing_fps
            next_publish_time = time.perf_counter()
        else:
            processing_interval = None
            next_publish_time = None

        while not stop_event.is_set():
            try:
                # Always read from the hardware continuously.
                raw_frame = self._read_from_hardware()
                capture_time = time.perf_counter()
                camera_event(self, "captured")

                # If processing_fps is configured, skip frames until the
                # next processing/publishing interval.
                if (
                    processing_interval is not None
                    and next_publish_time is not None
                    and capture_time < next_publish_time
                ):
                    failure_count = 0
                    continue

                # Only postprocess frames that will actually be published.
                processed_frame = self._postprocess_image(raw_frame)

                with self.frame_lock:
                    self.latest_frame = processed_frame
                    self.latest_timestamp = capture_time

                self.new_frame_event.set()
                camera_event(self, "published")

                # Schedule the next publication based on absolute time rather
                # than sleeping, preventing drift over long recordings.
                if processing_interval is not None:
                    next_publish_time += processing_interval

                    # If the reader fell behind, skip missed publication slots
                    # rather than publishing several frames back-to-back.
                    if next_publish_time <= capture_time:
                        missed_intervals = int(
                            (capture_time - next_publish_time) / processing_interval
                        ) + 1
                        next_publish_time += missed_intervals * processing_interval

                failure_count = 0

            except DeviceNotConnectedError:
                break

            except Exception as e:
                if failure_count <= 10:
                    failure_count += 1
                    logger.warning(
                        f"Error reading frame in background thread for {self}: {e}"
                    )
                else:
                    raise RuntimeError(
                        f"{self} exceeded maximum consecutive read failures."
                    ) from e

    def _start_read_thread(self) -> None:
        """Starts or restarts the background read thread if it's not running."""

        self._stop_read_thread()

        self.stop_event = Event()

        self.thread = Thread(
            target=self._read_loop,
            args=(),
            name=f"{self}_read_loop",
        )

        self.thread.daemon = True
        self.thread.start()

        time.sleep(0.1)

    def _stop_read_thread(self) -> None:
        """Signals the background read thread to stop and waits for it to join."""

        if self.stop_event is not None:
            self.stop_event.set()

        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=2.0)

            if self.thread.is_alive():
                logger.warning(
                    f"{self} read thread did not terminate within timeout."
                )

        self.thread = None
        self.stop_event = None

        with self.frame_lock:
            self.latest_frame = None
            self.latest_timestamp = None
            self.new_frame_event.clear()

    def _cleanup_resources(self) -> None:
        """Stop background reads and release the capture, including after partial setup."""

        read_thread = self.thread
        videocapture = self.videocapture

        try:
            self._stop_read_thread()

        finally:
            self.videocapture = None

            try:
                if videocapture is not None:
                    videocapture.release()

            finally:
                # Releasing the device may unblock a hardware read that outlived
                # the first bounded join in _stop_read_thread().
                if read_thread is not None and read_thread.is_alive():
                    read_thread.join(timeout=2.0)

                    if read_thread.is_alive():  # pragma: no cover
                        logger.warning(
                            f"{self} read thread remained alive after "
                            f"releasing the capture."
                        )

    @check_if_not_connected
    def async_read(self, timeout_ms: float = 200) -> NDArray[Any]:
        """
        Reads the latest available processed frame asynchronously.

        When `processing_fps` is configured, the event is triggered only when
        a new processed/published frame becomes available.
        """

        if self.thread is None or not self.thread.is_alive():
            raise RuntimeError(f"{self} read thread is not running.")

        if not self.new_frame_event.wait(timeout=timeout_ms / 1000.0):
            raise TimeoutError(
                f"Timed out waiting for frame from camera {self} after {timeout_ms} ms. "
                f"Read thread alive: {self.thread.is_alive()}."
            )

        with self.frame_lock:
            frame = self.latest_frame
            self.new_frame_event.clear()

        if frame is None:
            raise RuntimeError(
                f"Internal error: Event set but no frame available for {self}."
            )

        return frame

    @check_if_not_connected
    def read_latest(self, max_age_ms: int = 500) -> NDArray[Any]:
        return self.read_latest_with_timestamp(max_age_ms)[0]

    def read_latest_with_timestamp(self, max_age_ms: int = 500):
        """
        Return the most recent processed frame immediately.

        When `processing_fps` is configured, this returns the most recently
        published frame rather than every hardware frame.
        """

        if self.thread is None or not self.thread.is_alive():
            raise RuntimeError(f"{self} read thread is not running.")

        with self.frame_lock:
            frame = self.latest_frame
            timestamp = self.latest_timestamp

        if frame is None or timestamp is None:
            raise RuntimeError(
                f"{self} has not captured any frames yet."
            )

        age_ms = (time.perf_counter() - timestamp) * 1e3

        if age_ms > max_age_ms:
            raise TimeoutError(
                f"{self} latest frame is too old: {age_ms:.1f} ms "
                f"(max allowed: {max_age_ms} ms)."
            )

        return frame,timestamp

    def disconnect(self) -> None:
        """
        Disconnects from the camera and cleans up resources.
        """

        if not self.is_connected and self.thread is None:
            raise DeviceNotConnectedError(f"{self} not connected.")

        self._cleanup_resources()

        logger.info(f"{self} disconnected.")
