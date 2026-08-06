"""Project-owned Wuji Glove input adapter for wuji-sdk 2026.8.x.

The upstream ``wuji-retargeting`` adapter intentionally exposes only the
``hand_skeleton`` stream.  This adapter preserves this project's optional
offline skeleton pipeline and latency counters without modifying vendored
upstream code.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

import numpy as np


class WujiGloveDevice:
    def __init__(
        self,
        hand_side: Optional[str] = "right",
        device_name: str = "glove",
        sn: Optional[str] = None,
        stream: str = "hand_skeleton",
        sdk_log_level: Optional[str] = "error",
        emf_rate_divider: Optional[int] = None,
    ):
        import wuji_sdk
        from wuji_sdk import DeviceType, SdkManager, WujiGlove

        if hand_side is None:
            normalized_side = None
        else:
            normalized_side = hand_side.lower()
            if normalized_side not in {"left", "right"}:
                raise ValueError(f"hand_side must be left/right/None, got {hand_side!r}")
        stream = stream.lower()
        if stream not in {"hand_skeleton", "offline_hand_skeleton", "emf_poses"}:
            raise ValueError(f"Unsupported Wuji Glove stream {stream!r}")
        if stream != "hand_skeleton" and normalized_side is None:
            raise ValueError(f"hand_side is required for stream={stream!r}")
        if emf_rate_divider is not None and emf_rate_divider < 1:
            raise ValueError("emf_rate_divider must be at least 1")

        if sdk_log_level:
            wuji_sdk.set_log_level("warn" if sdk_log_level == "warning" else sdk_log_level)

        self._hand_side = normalized_side
        self._device_name = device_name
        self._stream = stream
        self._manager = SdkManager.instance()
        self._device = None
        self._sub = None
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

        try:
            if sn:
                self._device = self._manager.connect(sn=sn, device_name=device_name)
            else:
                gloves = [
                    d
                    for d in self._manager.scan()
                    if d.device_type == DeviceType.WujiGlove
                    or str(d.sn).upper().startswith("WG")
                ]
                if len(gloves) != 1:
                    listing = ", ".join(f"{d.sn}@{d.address}" for d in gloves) or "none"
                    raise RuntimeError(
                        f"Expected exactly one Wuji Glove, found {len(gloves)} ({listing}); "
                        "pass --glove-sn."
                    )
                self._device = self._manager.connect(
                    sn=str(gloves[0].sn), device_name=device_name
                )

            if emf_rate_divider is not None:
                divider_resource = self._device.emf_poses_rate_divider()
                previous_divider = int(divider_resource.get())
                if previous_divider != emf_rate_divider:
                    divider_resource.set(int(emf_rate_divider))
                print(
                    "Wuji Glove EMF rate divider: "
                    f"{previous_divider} -> {int(emf_rate_divider)}"
                )

            if stream == "hand_skeleton":
                self._sub = self._device.hand_skeleton().subscribe()
            elif stream == "offline_hand_skeleton":
                device_sn = sn or str(self._device.serial_number)
                self._offline_pipeline = WujiGlove.offline_pipeline(
                    device_sn, normalized_side
                )
                self._sub = self._device.emf_poses().subscribe()
            else:
                self._sub = self._device.emf_poses().subscribe()
        except BaseException:
            self.cleanup()
            raise

    def get_fingers_data(self) -> Dict[str, Optional[np.ndarray]]:
        if self._stream == "emf_poses":
            raise RuntimeError(
                "emf_poses is raw sensor data; use offline_hand_skeleton or hand_skeleton"
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
            self._debug_stats["last_cache_age_ms"] = (
                None
                if self._last_receive_monotonic is None
                else (time.monotonic() - self._last_receive_monotonic) * 1000.0
            )
            return self._last_data

        new_frames = 1
        drained = 0
        drain_start = time.perf_counter()
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

        skeleton = (
            self._offline_pipeline.hand_skeleton().compute(frame)
            if self._stream == "offline_hand_skeleton"
            else frame
        )
        keypoints = np.asarray(
            [joint.pose.position for joint in skeleton.joints], dtype=np.float32
        )
        if keypoints.shape != (21, 3) or not np.isfinite(keypoints).all():
            print(f"Warning: invalid glove skeleton shape/data {keypoints.shape}; skipping")
            return self._last_data

        side = self._hand_side or self._detect_hand_side(skeleton)
        result = {"left_fingers": None, "right_fingers": None}
        result[f"{side}_fingers"] = keypoints
        self._last_data = result
        return result

    def get_debug_stats(self):
        return dict(self._debug_stats)

    def cleanup(self):
        sub, self._sub = self._sub, None
        if sub is not None:
            try:
                sub.close()
            except Exception:
                pass
        device, self._device = self._device, None
        if device is not None:
            try:
                device.disconnect()
            except Exception:
                try:
                    self._manager.disconnect(device_name=self._device_name)
                except Exception:
                    pass
        self._offline_pipeline = None

    close = cleanup

    @staticmethod
    def _detect_hand_side(skeleton) -> str:
        frame_id = str(skeleton.header.frame_id)
        return "left" if frame_id.startswith("l") else "right"
