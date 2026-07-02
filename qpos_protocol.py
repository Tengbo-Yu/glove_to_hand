"""Newline-delimited JSON TCP protocol shared by the glove client and hand server.

The protocol supports two frame payloads:
- ready-to-apply hand joint targets (qpos, shape ``(5, 4)``)
- raw Wuji glove keypoints (shape ``(21, 3)``) for robot-side retargeting
"""

import json
import socket

import numpy as np


PROTOCOL_NAME = "glove-qpos-v1"
JOINT_MATRIX_SHAPE = (5, 4)
KEYPOINTS_SHAPE = (21, 3)


def make_hello_message(hand_side, sent_at):
    return {
        "type": "hello",
        "protocol": PROTOCOL_NAME,
        "hand_side": hand_side,
        "sent_at": float(sent_at),
    }


def make_qpos_message(seq, qpos, timestamp, debug=None):
    qpos = np.asarray(qpos, dtype=np.float64)
    if qpos.shape != JOINT_MATRIX_SHAPE:
        raise ValueError(f"qpos must have shape {JOINT_MATRIX_SHAPE}, got {qpos.shape}")
    message = {
        "type": "frame",
        "protocol": PROTOCOL_NAME,
        "seq": int(seq),
        "timestamp": float(timestamp),
        "qpos": qpos.tolist(),
    }
    if debug is not None:
        message["debug"] = debug
    return message


def make_keypoints_message(seq, keypoints, timestamp, hand_side, debug=None):
    keypoints = np.asarray(keypoints, dtype=np.float64)
    if keypoints.shape != KEYPOINTS_SHAPE:
        raise ValueError(f"keypoints must have shape {KEYPOINTS_SHAPE}, got {keypoints.shape}")
    message = {
        "type": "keypoints_frame",
        "protocol": PROTOCOL_NAME,
        "seq": int(seq),
        "timestamp": float(timestamp),
        "hand_side": hand_side,
        "keypoints": keypoints.tolist(),
    }
    if debug is not None:
        message["debug"] = debug
    return message


def encode_message(message):
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def decode_message(line):
    if isinstance(line, bytes):
        line = line.decode("utf-8")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("socket message must be a JSON object")

    protocol = message.get("protocol")
    if protocol != PROTOCOL_NAME:
        raise ValueError(f"unsupported protocol {protocol!r}; expected {PROTOCOL_NAME!r}")

    message_type = message.get("type")
    if message_type == "hello":
        return message
    if message_type == "frame":
        qpos = np.asarray(message.get("qpos"), dtype=np.float64)
        if qpos.shape != JOINT_MATRIX_SHAPE:
            raise ValueError(f"qpos must have shape {JOINT_MATRIX_SHAPE}, got {qpos.shape}")
        message["qpos"] = qpos
    elif message_type == "keypoints_frame":
        keypoints = np.asarray(message.get("keypoints"), dtype=np.float64)
        if keypoints.shape != KEYPOINTS_SHAPE:
            raise ValueError(f"keypoints must have shape {KEYPOINTS_SHAPE}, got {keypoints.shape}")
        message["keypoints"] = keypoints
    else:
        raise ValueError(f"unsupported message type {message_type!r}")

    message["seq"] = int(message.get("seq", -1))
    message["timestamp"] = float(message.get("timestamp", 0.0))
    return message


class SocketLineReader:
    """Read newline-delimited messages from a socket with a partial-line buffer.

    ``read_line`` returns one complete line (without the trailing newline), or
    ``None`` when the socket timed out before a full line arrived. Any partial
    bytes are retained across calls, so a timeout never corrupts framing. A
    closed peer raises ``EOFError``.
    """

    def __init__(self, sock):
        self._sock = sock
        self._buffer = b""

    def read_line(self):
        while b"\n" not in self._buffer:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                raise EOFError("socket closed")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line

    def read_available_line(self):
        """Return one complete line that is already available, without waiting."""
        while b"\n" not in self._buffer:
            previous_timeout = self._sock.gettimeout()
            try:
                self._sock.settimeout(0.0)
                try:
                    chunk = self._sock.recv(4096)
                except (BlockingIOError, socket.timeout):
                    return None
            finally:
                self._sock.settimeout(previous_timeout)
            if not chunk:
                raise EOFError("socket closed")
            self._buffer += chunk

        line, _, self._buffer = self._buffer.partition(b"\n")
        return line

    def read_available_lines(self):
        """Return all complete lines already available, without waiting."""
        previous_timeout = self._sock.gettimeout()
        try:
            self._sock.settimeout(0.0)
            while True:
                try:
                    chunk = self._sock.recv(65536)
                except (BlockingIOError, socket.timeout):
                    break
                if not chunk:
                    raise EOFError("socket closed")
                self._buffer += chunk
        finally:
            self._sock.settimeout(previous_timeout)

        lines = []
        while b"\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition(b"\n")
            lines.append(line)
        return lines
