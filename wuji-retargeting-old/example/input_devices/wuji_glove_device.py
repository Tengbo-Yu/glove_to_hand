"""Input device for Wuji Glove via wuji_sdk.

Connects to a Wuji Glove via wuji_sdk's SdkManager and subscribes to
HandSkeleton data (21 MediaPipe joints). Returns (21, 3) position arrays
compatible with the existing retargeting pipeline.

Usage:
    device = WujiGloveDevice(hand_side="right", device_name="glove")
    data = device.get_fingers_data()
    # data["right_fingers"] -> np.ndarray (21, 3)
"""

from typing import Dict, Optional
import time

import numpy as np

from .base import InputDeviceBase

try:
    import wuji_sdk
    from wuji_sdk import SdkManager, WujiGlove
    WUJI_SDK_AVAILABLE = True
except ImportError:
    WUJI_SDK_AVAILABLE = False


class WujiGloveDevice(InputDeviceBase):
    """Input device that reads Wuji Glove MediaPipe data via ``wuji_sdk``."""

    def __init__(
        self,
        hand_side: Optional[str] = "right",
        device_name: str = "glove",
        sn: Optional[str] = None,
        stream: str = "hand_skeleton",
        sdk_log_level: Optional[str] = "error",
    ):
        """Initialize the Wuji Glove input device.

        Args:
            hand_side: Hand side, ``"left"`` or ``"right"``. If ``None``,
                infer the side from the SDK ``frame_id``.
            device_name: SDK device name used for routing and handle management.
            sn: Device serial number. Required in multi-device setups; may be
                ``None`` when auto-connecting a single glove.
            stream: SDK stream used to produce 21-point skeletons. ``"hand_skeleton"``
                subscribes directly to the SDK virtual skeleton stream;
                ``"offline_hand_skeleton"`` subscribes to ``emf_poses`` and computes
                skeletons in this process at the caller's rate.
            sdk_log_level: Optional wuji_sdk log level, e.g. ``"error"`` or ``"off"``.
        """
        if not WUJI_SDK_AVAILABLE:
            raise ImportError(
                "wuji_sdk is not installed. "
                "Please install wuji_sdk to use WujiGloveDevice."
            )

        if hand_side is None:
            normalized_hand_side = None
        else:
            normalized_hand_side = hand_side.lower()
            if normalized_hand_side not in {"left", "right"}:
                raise ValueError(
                    f"hand_side must be 'left', 'right', or None, got {hand_side!r}"
                )

        stream = stream.lower()
        if stream not in {"hand_skeleton", "offline_hand_skeleton", "emf_poses"}:
            raise ValueError(
                "stream must be one of 'hand_skeleton', 'offline_hand_skeleton', or 'emf_poses', "
                f"got {stream!r}"
            )
        if stream in {"offline_hand_skeleton", "emf_poses"} and normalized_hand_side is None:
            raise ValueError(f"hand_side is required when stream={stream!r}")

        self._hand_side = normalized_hand_side
        self._device_name = device_name
        self._stream = stream
        self._offline_pipeline = None
        self._last_data: Dict[str, Optional[np.ndarray]] = {
            "left_fingers": None,
            "right_fingers": None,
        }
        self._debug_stats = {
            "polls": 0,
            "new_frames": 0,
            "cache_hits": 0,
            "drained_frames": 0,
            "last_poll_new_frames": 0,
            "last_poll_drained_frames": 0,
            "last_cache_age_ms": None,
            "last_recv_ms": 0.0,
            "last_drain_ms": 0.0,
        }
        self._last_receive_monotonic = None

        if sdk_log_level:
            wuji_sdk.set_log_level(sdk_log_level)

        manager = SdkManager.instance()
        if sn:
            self._device = manager.connect(sn=sn, device_name=device_name)
        else:
            self._device = manager.auto_connect(device_name=device_name)
        if stream == "hand_skeleton":
            self._sub = self._device.hand_skeleton().subscribe()
        elif stream == "offline_hand_skeleton":
            device_sn = sn or getattr(self._device, "serial_number", None)
            if device_sn is None:
                device_sn = self._device.sn().get()
            self._offline_pipeline = WujiGlove.offline_pipeline(device_sn, normalized_hand_side)
            self._sub = self._device.emf_poses().subscribe()
        else:
            self._sub = self._device.emf_poses().subscribe()

    def get_fingers_data(self) -> Dict[str, Optional[np.ndarray]]:
        """Return the latest non-blocking ``(21, 3)`` skeleton frame.

        Returns:
            {"left_fingers": np.ndarray | None, "right_fingers": np.ndarray | None}
            Returns the cached previous frame when no new data is available.
        """
        if self._stream == "emf_poses":
            raise RuntimeError(
                "get_fingers_data() cannot convert stream='emf_poses' to 21 keypoints. "
                "Use stream='offline_hand_skeleton' to compute skeletons from emf_poses, "
                "or stream='hand_skeleton' to use the SDK skeleton virtual stream."
            )

        self._debug_stats["polls"] += 1
        recv_start = time.perf_counter()
        frame = self._sub.recv()
        self._debug_stats["last_recv_ms"] = (time.perf_counter() - recv_start) * 1000.0
        if frame is None:
            self._debug_stats["cache_hits"] += 1
            self._debug_stats["last_poll_new_frames"] = 0
            self._debug_stats["last_poll_drained_frames"] = 0
            self._debug_stats["last_drain_ms"] = 0.0
            if self._last_receive_monotonic is None:
                self._debug_stats["last_cache_age_ms"] = None
            else:
                self._debug_stats["last_cache_age_ms"] = (
                    time.monotonic() - self._last_receive_monotonic
                ) * 1000.0
            return self._last_data

        new_frames = 1
        drained = 0
        drain_start = time.perf_counter()
        # Drain queue to keep only the latest frame,
        # preventing lag buildup when SDK pushes faster than we consume.
        while True:
            newer = self._sub.recv()
            if newer is None:
                break
            frame = newer
            new_frames += 1
            drained += 1
        self._debug_stats["last_drain_ms"] = (time.perf_counter() - drain_start) * 1000.0
        self._debug_stats["new_frames"] += new_frames
        self._debug_stats["drained_frames"] += drained
        self._debug_stats["last_poll_new_frames"] = new_frames
        self._debug_stats["last_poll_drained_frames"] = drained
        self._debug_stats["last_cache_age_ms"] = 0.0
        self._last_receive_monotonic = time.monotonic()

        if self._stream == "offline_hand_skeleton":
            skeleton = self._offline_pipeline.hand_skeleton().compute(frame)
        else:
            skeleton = frame

        # Extract 21 joint positions as (x, y, z) coordinates.
        keypoints = np.array(
            [j.pose.position for j in skeleton.joints],
            dtype=np.float32,
        )
        if keypoints.shape != (21, 3):
            print(f"Warning: unexpected skeleton shape {keypoints.shape}, skipping frame")
            return self._last_data

        # Infer the active hand side when it is not fixed by the caller.
        hand_side = self._hand_side
        if hand_side is None:
            hand_side = self._detect_hand_side(skeleton)

        result: Dict[str, Optional[np.ndarray]] = {
            "left_fingers": None,
            "right_fingers": None,
        }
        result[f"{hand_side}_fingers"] = keypoints
        self._last_data = result
        return result

    def get_debug_stats(self):
        """Return non-mutating Wuji SDK freshness/debug counters."""
        return dict(self._debug_stats)

    def cleanup(self):
        """Release SDK resources."""
        if self._sub is not None:
            try:
                self._sub.close()
            except Exception:
                pass
            self._sub = None
        if self._device is not None:
            try:
                self._device.disconnect()
            except Exception:
                pass
            self._device = None
        self._offline_pipeline = None

    @staticmethod
    def _detect_hand_side(skeleton) -> str:
        """Infer the hand side from ``skeleton.header.frame_id``.

        Returns ``"left"`` for ``"l_wrist"`` and ``"right"`` for
        ``"r_wrist"``. Falls back to ``"right"`` when the frame cannot be
        identified.
        """
        frame_id = skeleton.header.frame_id
        if frame_id.startswith("l"):
            return "left"
        return "right"
