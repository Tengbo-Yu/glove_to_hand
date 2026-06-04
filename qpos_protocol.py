"""Newline-delimited JSON TCP protocol shared by the glove client and hand server.

The glove client performs retargeting and streams ready-to-apply (5, 4) joint
targets (qpos). The hand server only writes those targets to the hand, so it
needs neither wuji-retargeting nor the glove SDK.
"""

import json
import socket

import numpy as np


PROTOCOL_NAME = "glove-qpos-v1"
JOINT_MATRIX_SHAPE = (5, 4)


def make_hello_message(hand_side, sent_at):
    return {
        "type": "hello",
        "protocol": PROTOCOL_NAME,
        "hand_side": hand_side,
        "sent_at": float(sent_at),
    }


def make_qpos_message(seq, qpos, timestamp):
    qpos = np.asarray(qpos, dtype=np.float64)
    if qpos.shape != JOINT_MATRIX_SHAPE:
        raise ValueError(f"qpos must have shape {JOINT_MATRIX_SHAPE}, got {qpos.shape}")
    return {
        "type": "frame",
        "protocol": PROTOCOL_NAME,
        "seq": int(seq),
        "timestamp": float(timestamp),
        "qpos": qpos.tolist(),
    }


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
    if message_type != "frame":
        raise ValueError(f"unsupported message type {message_type!r}")

    qpos = np.asarray(message.get("qpos"), dtype=np.float64)
    if qpos.shape != JOINT_MATRIX_SHAPE:
        raise ValueError(f"qpos must have shape {JOINT_MATRIX_SHAPE}, got {qpos.shape}")
    message["qpos"] = qpos
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
