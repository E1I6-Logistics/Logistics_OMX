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

from dataclasses import dataclass
from pathlib import Path

from ..configs import CameraConfig, ColorMode, Cv2Backends, Cv2Rotation

__all__ = ["OpenCVCameraConfig", "ColorMode", "Cv2Rotation", "Cv2Backends"]


@CameraConfig.register_subclass("opencv")
@dataclass
class OpenCVCameraConfig(CameraConfig):
    """Configuration class for OpenCV-based camera devices or video files.

    This class provides configuration options for cameras accessed through OpenCV,
    supporting both physical camera devices and video files. It includes settings
    for resolution, frame rate, color mode, image rotation, and processing rate.

    Example configurations:
    ```python
    # Basic configurations
    OpenCVCameraConfig(0, 30, 1280, 720)   # 1280x720 @ 30FPS

    # Hardware capture at 30FPS, process/publish frames at 15FPS
    OpenCVCameraConfig(
        0,
        30,
        1280,
        720,
        processing_fps=15,
    )

    # Advanced configurations with FOURCC format
    OpenCVCameraConfig(
        128422271347,
        30,
        640,
        480,
        rotation=Cv2Rotation.ROTATE_90,
        fourcc="MJPG",
    )

    OpenCVCameraConfig(
        0,
        30,
        1280,
        720,
        fourcc="YUYV",
    )
    ```

    Attributes:
        index_or_path: Either an integer representing the camera device index,
                      or a Path object pointing to a video file.
        fps: Requested frames per second for the hardware color stream.
        width: Requested frame width in pixels for the color stream.
        height: Requested frame height in pixels for the color stream.
        color_mode: Color mode for image output (RGB or BGR). Defaults to RGB.
        rotation: Image rotation setting (0°, 90°, 180°, or 270°). Defaults to no rotation.
        warmup_s: Time reading frames before returning from connect (in seconds).
        processing_fps: Maximum rate at which captured frames are processed and
                        published to LeRobot. If None, every captured frame is processed.
        fourcc: FOURCC code for video format (e.g., "MJPG", "YUYV", "I420").
                Defaults to None (auto-detect).
        backend: OpenCV backend identifier.
                 Defaults to ANY.

    Note:
        - Only 3-channel color output (RGB/BGR) is currently supported.
        - FOURCC codes must be 4-character strings.
        - `fps` controls the hardware capture rate.
        - `processing_fps` controls the rate at which frames are made available
          to the rest of LeRobot.
        - Setting `processing_fps` lower than `fps` does not change the camera's
          hardware capture rate.
    """

    index_or_path: int | Path
    color_mode: ColorMode = ColorMode.RGB
    rotation: Cv2Rotation = Cv2Rotation.NO_ROTATION
    warmup_s: int = 1
    processing_fps: float | None = None
    fourcc: str | None = None
    backend: Cv2Backends = Cv2Backends.ANY

    def __post_init__(self) -> None:
        self.color_mode = ColorMode(self.color_mode)
        self.rotation = Cv2Rotation(self.rotation)
        self.backend = Cv2Backends(self.backend)

        if self.processing_fps is not None:
            if self.processing_fps <= 0:
                raise ValueError("`processing_fps` must be greater than 0.")

            if self.fps is not None and self.processing_fps > self.fps:
                raise ValueError(
                    f"`processing_fps` ({self.processing_fps}) cannot be greater than "
                    f"hardware `fps` ({self.fps})."
                )

        if self.fourcc is not None and (
            not isinstance(self.fourcc, str) or len(self.fourcc) != 4
        ):
            raise ValueError(
                f"`fourcc` must be a 4-character string (e.g., 'MJPG', 'YUYV'), "
                f"but '{self.fourcc}' is provided."
            )
