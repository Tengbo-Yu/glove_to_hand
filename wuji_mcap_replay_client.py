"""Replay Wuji hand command telemetry from a DataCollector MCAP file."""

from __future__ import annotations

import argparse
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import msgpack
import numpy as np

from qpos_protocol import encode_message, make_hello_message, make_qpos_message


LEFT_HAND_COMMAND_TOPIC = "/wuji/hand/left/command"
RIGHT_HAND_COMMAND_TOPIC = "/wuji/hand/right/command"
LEFT_GLOVE_COMMAND_TOPIC = "/wuji/glove/left/command"
RIGHT_GLOVE_COMMAND_TOPIC = "/wuji/glove/right/command"
JOINT_MATRIX_SHAPE = (5, 4)

TOPICS_BY_SOURCE = {
    "hand_command": {
        "left": LEFT_HAND_COMMAND_TOPIC,
        "right": RIGHT_HAND_COMMAND_TOPIC,
    },
    "glove_command": {
        "left": LEFT_GLOVE_COMMAND_TOPIC,
        "right": RIGHT_GLOVE_COMMAND_TOPIC,
    },
}

QPOS_FIELD_BY_SOURCE = {
    "hand_command": "received_qpos_5x4",
    "glove_command": "retargeted_qpos_5x4",
}


@dataclass(frozen=True)
class ReplayFrame:
    hand_side: str
    sequence: int
    timestamp_ns: int
    qpos: np.ndarray
    topic: str


@dataclass(frozen=True)
class ScheduledReplayFrame:
    hand_side: str
    sequence: int
    timestamp_ns: int
    qpos: np.ndarray
    topic: str
    replay_time_sec: float


def decode_msgpack_payload(payload_bytes: bytes, topic: str) -> dict:
    payload = msgpack.unpackb(payload_bytes, raw=False)
    if not isinstance(payload, dict):
        raise ValueError(f"{topic} payload must be a msgpack map, got {type(payload).__name__}")
    return payload


def topic_to_hand_side(topic: str, source: str) -> str:
    for hand_side, source_topic in TOPICS_BY_SOURCE[source].items():
        if topic == source_topic:
            return hand_side
    raise ValueError(f"unsupported {source} topic: {topic}")


def qpos_from_payload(payload: dict, field_name: str) -> np.ndarray:
    qpos = np.asarray(payload.get(field_name), dtype=np.float64)
    if qpos.shape != JOINT_MATRIX_SHAPE:
        raise ValueError(f"{field_name} must have shape {JOINT_MATRIX_SHAPE}, got {qpos.shape}")
    return qpos


def sequence_from_payload(payload: dict, mcap_sequence: int) -> int:
    for key in ("glove_seq", "seq"):
        if key in payload and payload[key] is not None:
            return int(payload[key])
    return int(mcap_sequence)


def timestamp_from_message(payload: dict, message, timing: str) -> int:
    if timing == "log_time":
        return int(message.log_time)
    if timing == "source_time":
        return int(message.publish_time)
    if timing == "apply_time":
        value = payload.get("apply_timestamp_ns")
        if value is None:
            raise ValueError("apply_time timing requires payload apply_timestamp_ns")
        return int(value)
    raise ValueError(f"unsupported timing mode: {timing}")


def replay_frame_from_message(topic: str, message, *, source: str, timing: str) -> ReplayFrame:
    payload = decode_msgpack_payload(message.data, topic)
    hand_side = topic_to_hand_side(topic, source)
    payload_side = payload.get("hand_side")
    if payload_side is not None and payload_side != hand_side:
        raise ValueError(f"{topic} payload hand_side {payload_side!r} does not match topic side {hand_side!r}")
    qpos = qpos_from_payload(payload, QPOS_FIELD_BY_SOURCE[source])
    return ReplayFrame(
        hand_side=hand_side,
        sequence=sequence_from_payload(payload, message.sequence),
        timestamp_ns=timestamp_from_message(payload, message, timing),
        qpos=qpos,
        topic=topic,
    )


def iter_replay_frames(
    mcap_path: str | Path,
    *,
    source: str = "hand_command",
    timing: str = "log_time",
) -> Iterable[ReplayFrame]:
    if source not in TOPICS_BY_SOURCE:
        raise ValueError(f"unsupported replay source: {source}")

    from mcap.reader import make_reader

    topics = list(TOPICS_BY_SOURCE[source].values())
    frames: list[ReplayFrame] = []
    with Path(mcap_path).open("rb") as f:
        reader = make_reader(f)
        for _, channel, message in reader.iter_messages(topics=topics):
            frames.append(replay_frame_from_message(channel.topic, message, source=source, timing=timing))

    yield from sorted(frames, key=lambda frame: (frame.timestamp_ns, frame.hand_side, frame.sequence))


def schedule_replay_frames(
    frames: Sequence[ReplayFrame],
    *,
    start_sec: float = 0.0,
    duration_sec: float | None = None,
) -> list[ScheduledReplayFrame]:
    if start_sec < 0:
        raise ValueError("start_sec must be >= 0")
    if duration_sec is not None and duration_sec < 0:
        raise ValueError("duration_sec must be >= 0")
    if not frames:
        return []

    sorted_frames = sorted(frames, key=lambda frame: (frame.timestamp_ns, frame.hand_side, frame.sequence))
    base_timestamp_ns = sorted_frames[0].timestamp_ns
    start_timestamp_ns = base_timestamp_ns + int(start_sec * 1_000_000_000)
    end_timestamp_ns = None
    if duration_sec is not None:
        end_timestamp_ns = start_timestamp_ns + int(duration_sec * 1_000_000_000)

    scheduled: list[ScheduledReplayFrame] = []
    for frame in sorted_frames:
        if frame.timestamp_ns < start_timestamp_ns:
            continue
        if end_timestamp_ns is not None and frame.timestamp_ns >= end_timestamp_ns:
            continue
        replay_time_sec = (frame.timestamp_ns - start_timestamp_ns) / 1_000_000_000
        scheduled.append(
            ScheduledReplayFrame(
                hand_side=frame.hand_side,
                sequence=frame.sequence,
                timestamp_ns=frame.timestamp_ns,
                qpos=frame.qpos,
                topic=frame.topic,
                replay_time_sec=replay_time_sec,
            )
        )
    return scheduled


def playback_sleep_seconds(
    previous_replay_time_sec: float,
    current_replay_time_sec: float,
    *,
    speed: float,
    max_gap_sec: float | None,
) -> float:
    if speed <= 0:
        raise ValueError("speed must be > 0")
    if max_gap_sec is not None and max_gap_sec < 0:
        raise ValueError("max_gap_sec must be >= 0")
    sleep_sec = max(current_replay_time_sec - previous_replay_time_sec, 0.0) / speed
    if max_gap_sec is not None:
        sleep_sec = min(sleep_sec, max_gap_sec)
    return sleep_sec


def send_replay_frame(sock: socket.socket, frame: ReplayFrame | ScheduledReplayFrame) -> None:
    sock.sendall(encode_message(make_qpos_message(frame.sequence, frame.qpos, frame.timestamp_ns / 1_000_000_000)))


def open_socket(host: str, port: int, connect_timeout: float) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=connect_timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(None)
    return sock


def summarize_frames(frames: Sequence[ReplayFrame | ScheduledReplayFrame]) -> dict[str, int]:
    counts = {"left": 0, "right": 0}
    for frame in frames:
        counts[frame.hand_side] = counts.get(frame.hand_side, 0) + 1
    return counts


def replay_scheduled_frames(
    frames: Sequence[ScheduledReplayFrame],
    *,
    left_host: str,
    left_port: int,
    right_host: str,
    right_port: int,
    speed: float,
    max_gap_sec: float | None,
    connect_timeout: float,
    dry_run: bool,
    sleep_fn=time.sleep,
) -> None:
    counts = summarize_frames(frames)
    if dry_run:
        print(
            "Dry run: "
            f"frames={len(frames)} left={counts.get('left', 0)} right={counts.get('right', 0)} "
            f"duration_sec={(frames[-1].replay_time_sec if frames else 0.0):.3f}"
        )
        return

    sockets: dict[str, socket.socket] = {}
    try:
        if counts.get("left", 0):
            sockets["left"] = open_socket(left_host, left_port, connect_timeout)
            sockets["left"].sendall(encode_message(make_hello_message("left", time.time())))
        if counts.get("right", 0):
            sockets["right"] = open_socket(right_host, right_port, connect_timeout)
            sockets["right"].sendall(encode_message(make_hello_message("right", time.time())))

        previous_replay_time_sec = 0.0
        for index, frame in enumerate(frames, start=1):
            sleep_sec = playback_sleep_seconds(
                previous_replay_time_sec,
                frame.replay_time_sec,
                speed=speed,
                max_gap_sec=max_gap_sec,
            )
            if sleep_sec > 0:
                sleep_fn(sleep_sec)
            previous_replay_time_sec = frame.replay_time_sec
            send_replay_frame(sockets[frame.hand_side], frame)
            if index == 1 or index == len(frames) or index % 100 == 0:
                print(
                    f"sent={index}/{len(frames)} side={frame.hand_side} seq={frame.sequence} "
                    f"t={frame.replay_time_sec:.3f}s"
                )
    finally:
        for sock in sockets.values():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay Wuji hand commands from a DataCollector MCAP episode.")
    parser.add_argument("mcap_path", type=Path, help="Input DataCollector episode.mcap path.")
    parser.add_argument("--source", choices=("hand_command", "glove_command"), default="hand_command")
    parser.add_argument("--timing", choices=("log_time", "source_time", "apply_time"), default="log_time")
    parser.add_argument("--left-host", default="127.0.0.1")
    parser.add_argument("--left-port", type=int, default=8765)
    parser.add_argument("--right-host", default="127.0.0.1")
    parser.add_argument("--right-port", type=int, default=8766)
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier.")
    parser.add_argument("--start-sec", type=float, default=0.0, help="Start offset relative to first frame.")
    parser.add_argument("--duration-sec", type=float, default=None, help="Optional replay duration after start-sec.")
    parser.add_argument("--max-gap-sec", type=float, default=None, help="Optional cap for sleeps between frames.")
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true", help="Inspect replay frames without opening TCP sockets.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    frames = list(iter_replay_frames(args.mcap_path, source=args.source, timing=args.timing))
    scheduled = schedule_replay_frames(frames, start_sec=args.start_sec, duration_sec=args.duration_sec)
    if not scheduled:
        print("No Wuji replay frames selected.")
        return 1
    replay_scheduled_frames(
        scheduled,
        left_host=args.left_host,
        left_port=args.left_port,
        right_host=args.right_host,
        right_port=args.right_port,
        speed=args.speed,
        max_gap_sec=args.max_gap_sec,
        connect_timeout=args.connect_timeout,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
