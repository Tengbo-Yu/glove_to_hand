import argparse
import asyncio
import json
import time

import numpy as np


THUMB_MAP = [0, 1, 3, 4]
FINGER_MAP = [0, 1, 2, 3]
SIDE_SWAY_JOINT = 1
JOINT_MATRIX_SHAPE = (5, 4)
PROTOCOL_NAME = "glove-to-hand-v1"


def parse_joint_matrix(value, default):
    if value is None:
        return np.full(JOINT_MATRIX_SHAPE, default, dtype=np.float64)

    values = [float(item.strip()) for item in value.split(",") if item.strip()]
    if len(values) != 20:
        raise argparse.ArgumentTypeError(
            f"expected 20 comma-separated values for a 5x4 joint matrix, got {len(values)}"
        )
    return np.asarray(values, dtype=np.float64).reshape(JOINT_MATRIX_SHAPE)


def format_joint_matrix_help(name):
    return (
        f"{name}: 20 comma-separated values in row-major order: "
        "thumb_j0,thumb_j1,thumb_j2,thumb_j3,"
        "index_j0,index_j1,index_j2,index_j3,"
        "middle_j0,middle_j1,middle_j2,middle_j3,"
        "ring_j0,ring_j1,ring_j2,ring_j3,"
        "pinky_j0,pinky_j1,pinky_j2,pinky_j3"
    )


def frame_to_array(frame):
    out = np.zeros(JOINT_MATRIX_SHAPE, dtype=np.float64)
    for finger_index, finger in enumerate(frame.fingers):
        angles = np.asarray(finger.angles, dtype=np.float64)
        if finger_index == 0:
            out[finger_index, :] = angles[THUMB_MAP]
        else:
            out[finger_index, :] = angles[FINGER_MAP]
    return out


def confidence_array(frame):
    return np.asarray([finger.confidence for finger in frame.fingers], dtype=np.float64)


def apply_confidence(target, previous, conf, threshold):
    mask = conf >= threshold
    merged = previous.copy()
    merged[mask, :] = target[mask, :]
    return merged


def apply_velocity_limit(target, previous, max_velocity, dt):
    if max_velocity <= 0 or dt <= 0:
        return target
    max_step = max_velocity * dt
    return previous + np.clip(target - previous, -max_step, max_step)


async def read_neutral(sub, n):
    samples = []
    for _ in range(n):
        samples.append(frame_to_array(await sub.recv_async()))
    return np.mean(np.stack(samples), axis=0)


async def recv_latest(sub, timeout=None):
    frame = await asyncio.wait_for(sub.recv_async(), timeout=timeout)
    dropped = 0
    while True:
        latest = sub.recv()
        if latest is None:
            return frame, dropped
        frame = latest
        dropped += 1


async def home_hand(controller, duration, rate):
    start = controller.get_joint_actual_position().astype(np.float64)
    zero = np.zeros(JOINT_MATRIX_SHAPE, dtype=np.float64)
    interval = 1.0 / rate
    steps = max(1, int(duration * rate))
    for step in range(steps):
        alpha = (step + 1) / steps
        target = (1.0 - alpha) * start + alpha * zero
        controller.set_joint_target_position(target)
        await asyncio.sleep(interval)
    controller.set_joint_target_position(zero)


def _coerce_joint_matrix(value, name):
    array = np.asarray(value, dtype=np.float64)
    if array.shape != JOINT_MATRIX_SHAPE:
        raise ValueError(f"{name} must have shape {JOINT_MATRIX_SHAPE}, got {array.shape}")
    return array


def _coerce_confidence(value):
    array = np.asarray(value, dtype=np.float64)
    if array.shape != (JOINT_MATRIX_SHAPE[0],):
        raise ValueError(f"confidence must have shape ({JOINT_MATRIX_SHAPE[0]},), got {array.shape}")
    return array


def make_hello_message(glove_name):
    return {
        "type": "hello",
        "protocol": PROTOCOL_NAME,
        "glove_name": glove_name,
        "joint_matrix_shape": list(JOINT_MATRIX_SHAPE),
        "sent_at": time.time(),
    }


def make_frame_message(seq, angles, confidence, timestamp=None):
    angles = _coerce_joint_matrix(angles, "angles")
    confidence = _coerce_confidence(confidence)
    return {
        "type": "frame",
        "protocol": PROTOCOL_NAME,
        "seq": int(seq),
        "timestamp": time.time() if timestamp is None else float(timestamp),
        "angles": angles.tolist(),
        "confidence": confidence.tolist(),
    }


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

    message["angles"] = _coerce_joint_matrix(message.get("angles"), "angles")
    message["confidence"] = _coerce_confidence(message.get("confidence"))
    message["seq"] = int(message.get("seq", -1))
    message["timestamp"] = float(message.get("timestamp", 0.0))
    return message


async def write_message(writer, message):
    writer.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
    await writer.drain()


async def read_message(reader, timeout=None):
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    if not line:
        raise EOFError("socket closed")
    return decode_message(line)
