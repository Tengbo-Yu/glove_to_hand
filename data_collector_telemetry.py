"""Non-blocking DataCollector telemetry helpers for Wuji glove/hand streams."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import numpy as np


FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]
JOINT_NAMES = ["MCP_aa", "MCP_fe", "PIP", "DIP"]
JOINT_MATRIX_SHAPE = (5, 4)

DEFAULT_STREAM_PORTS = {
    ("glove_command", "left"): 6011,
    ("glove_command", "right"): 6012,
    ("hand_command", "left"): 6013,
    ("hand_command", "right"): 6014,
    ("hand_state", "left"): 6015,
    ("hand_state", "right"): 6016,
}

DEFAULT_STREAM_NAMES = {
    ("glove_command", "left"): "wuji_left_glove_command",
    ("glove_command", "right"): "wuji_right_glove_command",
    ("hand_command", "left"): "wuji_left_hand_command",
    ("hand_command", "right"): "wuji_right_hand_command",
    ("hand_state", "left"): "wuji_left_hand_state",
    ("hand_state", "right"): "wuji_right_hand_state",
}


def default_endpoint(stream_kind: str, hand_side: str, host: str) -> str:
    port = DEFAULT_STREAM_PORTS[(stream_kind, hand_side)]
    return f"tcp://{host}:{port}"


def default_source(stream_kind: str, hand_side: str) -> str:
    return DEFAULT_STREAM_NAMES[(stream_kind, hand_side)]


def seconds_to_ns(timestamp_s: float) -> int:
    return int(float(timestamp_s) * 1_000_000_000)


def qpos_fields(prefix: str, qpos: Any) -> dict[str, Any]:
    qpos_array = np.asarray(qpos, dtype=np.float64)
    if qpos_array.shape != JOINT_MATRIX_SHAPE:
        raise ValueError(f"{prefix} must have shape {JOINT_MATRIX_SHAPE}, got {qpos_array.shape}")
    return {
        f"{prefix}_5x4": qpos_array.tolist(),
        f"{prefix}_flat20": qpos_array.reshape(-1).tolist(),
    }


def build_glove_command_payload(
    *,
    hand_side: str,
    seq: int,
    qpos: Any,
    fingers_pose: Any,
    config_path: str,
    glove_device_name: str,
    glove_sn: str,
    tcp_target: str,
    protocol: str,
    telemetry_dropped_count: int,
) -> dict[str, Any]:
    fingers_pose_array = np.asarray(fingers_pose, dtype=np.float64)
    payload: dict[str, Any] = {
        "hand_side": hand_side,
        "seq": int(seq),
        "finger_names": FINGER_NAMES,
        "joint_names": JOINT_NAMES,
        "fingers_pose": fingers_pose_array.tolist(),
        "fingers_pose_shape": list(fingers_pose_array.shape),
        "retarget_config": str(config_path),
        "glove_device_name": glove_device_name,
        "glove_sn": glove_sn,
        "tcp_target": tcp_target,
        "protocol": protocol,
        "telemetry_dropped_count": int(telemetry_dropped_count),
    }
    payload.update(qpos_fields("retargeted_qpos", qpos))
    return payload


class DataCollectorTelemetryPublisher:
    def __init__(
        self,
        *,
        endpoint: str,
        source: str,
        frame_id: str,
        socket: Any | None = None,
        packer: Callable[[dict[str, Any]], bytes] | None = None,
        again_exception: type[BaseException] | tuple[type[BaseException], ...] | None = None,
        noblock_flag: int | None = None,
        snd_hwm: int = 10,
    ):
        self.endpoint = endpoint
        self.source = source
        self.frame_id = frame_id
        self.dropped_count = 0

        if packer is None:
            try:
                import msgpack
            except ImportError as exc:
                raise RuntimeError("msgpack is required for DataCollector telemetry") from exc

            packer = lambda payload: msgpack.packb(payload, use_bin_type=True)
        self._packer = packer

        if socket is None:
            try:
                import zmq
            except ImportError as exc:
                raise RuntimeError("pyzmq is required for DataCollector telemetry") from exc

            self._context = zmq.Context.instance()
            self._socket = self._context.socket(zmq.PUSH)
            self._socket.setsockopt(zmq.LINGER, 0)
            self._socket.setsockopt(zmq.SNDHWM, snd_hwm)
            self._socket.connect(endpoint)
            self._again_exception = zmq.Again if again_exception is None else again_exception
            self._noblock_flag = zmq.NOBLOCK if noblock_flag is None else noblock_flag
        else:
            self._context = None
            self._socket = socket
            self._again_exception = RuntimeError if again_exception is None else again_exception
            self._noblock_flag = 0 if noblock_flag is None else noblock_flag

    def publish(
        self,
        *,
        sequence: int,
        payload: dict[str, Any],
        source_timestamp_ns: int | None = None,
    ) -> bool:
        if source_timestamp_ns is None:
            source_timestamp_ns = time.time_ns()
        header = {
            "sequence": int(sequence),
            "source_timestamp_ns": int(source_timestamp_ns),
            "source": self.source,
            "encoding": "msgpack",
            "frame_id": self.frame_id,
        }
        parts = [
            json.dumps(header, separators=(",", ":")).encode("utf-8"),
            self._packer(payload),
        ]
        try:
            self._socket.send_multipart(parts, flags=self._noblock_flag)
        except self._again_exception:
            self.dropped_count += 1
            return False
        return True

    def close(self) -> None:
        close = getattr(self._socket, "close", None)
        if callable(close):
            close(linger=0)
