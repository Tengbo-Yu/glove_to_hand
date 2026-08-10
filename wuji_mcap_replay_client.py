"""Replay Hand 2 qpos targets from a Hammerhead DataCollector MCAP."""

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


TOPICS_BY_SOURCE = {
    "hand_command": {
        "left": "/wuji/hand/left/command",
        "right": "/wuji/hand/right/command",
    },
    "glove_command": {
        "left": "/wuji/glove/left/command",
        "right": "/wuji/glove/right/command",
    },
}
QPOS_FIELDS_BY_SOURCE = {
    "hand_command": ("received_qpos_5x4", "target_qpos_5x4"),
    "glove_command": ("retargeted_qpos_5x4",),
}


@dataclass(frozen=True)
class ReplayFrame:
    hand_side: str
    sequence: int
    timestamp_ns: int
    qpos: np.ndarray
    topic: str


@dataclass(frozen=True)
class ScheduledReplayFrame(ReplayFrame):
    replay_time_sec: float


def _decode_payload(data: bytes, topic: str) -> dict:
    payload = msgpack.unpackb(data, raw=False)
    if not isinstance(payload, dict):
        raise ValueError(f"{topic} payload must be a msgpack map")
    return payload


def _topic_side(topic: str, source: str) -> str:
    for side, candidate in TOPICS_BY_SOURCE[source].items():
        if topic == candidate:
            return side
    raise ValueError(f"unsupported {source} topic: {topic}")


def _qpos_from_payload(payload: dict, source: str) -> np.ndarray:
    field_name = None
    value = None
    for candidate in QPOS_FIELDS_BY_SOURCE[source]:
        if payload.get(candidate) is not None:
            field_name = candidate
            value = payload[candidate]
            break
    if field_name is None:
        fields = ", ".join(QPOS_FIELDS_BY_SOURCE[source])
        raise ValueError(f"payload has no replayable qpos field ({fields})")
    qpos = np.asarray(value, dtype=np.float64)
    if qpos.shape != (5, 4):
        raise ValueError(f"{field_name} must have shape (5, 4), got {qpos.shape}")
    if not np.isfinite(qpos).all():
        raise ValueError(f"{field_name} must contain only finite values")
    return qpos


def _timestamp_ns(payload: dict, message, timing: str) -> int:
    if timing == "log_time":
        return int(message.log_time)
    if timing == "source_time":
        return int(message.publish_time)
    if timing == "apply_time":
        value = payload.get("apply_timestamp_ns")
        if value is None:
            raise ValueError("apply_time requires payload apply_timestamp_ns")
        return int(value)
    raise ValueError(f"unsupported timing mode: {timing}")


def replay_frame_from_message(topic, message, *, source, timing) -> ReplayFrame:
    payload = _decode_payload(message.data, topic)
    side = _topic_side(topic, source)
    if payload.get("hand_side") not in (None, side):
        raise ValueError(f"{topic} payload hand_side does not match its topic")
    sequence = payload.get("glove_seq", payload.get("seq", message.sequence))
    return ReplayFrame(
        hand_side=side,
        sequence=int(sequence),
        timestamp_ns=_timestamp_ns(payload, message, timing),
        qpos=_qpos_from_payload(payload, source),
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

    frames = []
    with Path(mcap_path).open("rb") as stream:
        for _, channel, message in make_reader(stream).iter_messages(
            topics=list(TOPICS_BY_SOURCE[source].values())
        ):
            frames.append(
                replay_frame_from_message(
                    channel.topic, message, source=source, timing=timing
                )
            )
    yield from sorted(
        frames, key=lambda frame: (frame.timestamp_ns, frame.hand_side, frame.sequence)
    )


def schedule_replay_frames(
    frames: Sequence[ReplayFrame],
    *,
    start_sec: float = 0.0,
    duration_sec: float | None = None,
) -> list[ScheduledReplayFrame]:
    if start_sec < 0 or (duration_sec is not None and duration_sec < 0):
        raise ValueError("start_sec and duration_sec must be non-negative")
    if not frames:
        return []
    ordered = sorted(
        frames, key=lambda frame: (frame.timestamp_ns, frame.hand_side, frame.sequence)
    )
    start_ns = ordered[0].timestamp_ns + int(start_sec * 1e9)
    end_ns = None if duration_sec is None else start_ns + int(duration_sec * 1e9)
    return [
        ScheduledReplayFrame(
            hand_side=frame.hand_side,
            sequence=frame.sequence,
            timestamp_ns=frame.timestamp_ns,
            qpos=frame.qpos,
            topic=frame.topic,
            replay_time_sec=(frame.timestamp_ns - start_ns) / 1e9,
        )
        for frame in ordered
        if frame.timestamp_ns >= start_ns
        and (end_ns is None or frame.timestamp_ns < end_ns)
    ]


def playback_sleep_seconds(previous, current, *, speed, max_gap_sec):
    if speed <= 0 or (max_gap_sec is not None and max_gap_sec < 0):
        raise ValueError("speed must be positive and max_gap_sec non-negative")
    delay = max(current - previous, 0.0) / speed
    return delay if max_gap_sec is None else min(delay, max_gap_sec)


def send_replay_frame(sock, frame):
    sock.sendall(
        encode_message(
            make_qpos_message(
                frame.sequence,
                frame.qpos,
                frame.timestamp_ns / 1e9,
                frame.hand_side,
            )
        )
    )


def _open_socket(host, port, timeout):
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(None)
    return sock


def replay_scheduled_frames(frames, args, sleep_fn=time.sleep):
    counts = {side: sum(frame.hand_side == side for frame in frames) for side in ("left", "right")}
    if args.dry_run:
        duration = frames[-1].replay_time_sec if frames else 0.0
        print(f"Dry run: frames={len(frames)} left={counts['left']} right={counts['right']} duration_sec={duration:.3f}")
        return

    sockets = {}
    try:
        for side in ("left", "right"):
            if counts[side]:
                sock = _open_socket(
                    getattr(args, f"{side}_host"),
                    getattr(args, f"{side}_port"),
                    args.connect_timeout,
                )
                sock.sendall(encode_message(make_hello_message(side, time.time())))
                sockets[side] = sock
        previous = 0.0
        for index, frame in enumerate(frames, 1):
            delay = playback_sleep_seconds(
                previous,
                frame.replay_time_sec,
                speed=args.speed,
                max_gap_sec=args.max_gap_sec,
            )
            if delay:
                sleep_fn(delay)
            previous = frame.replay_time_sec
            send_replay_frame(sockets[frame.hand_side], frame)
            if index == 1 or index == len(frames) or index % 100 == 0:
                print(f"sent={index}/{len(frames)} side={frame.hand_side} seq={frame.sequence} t={frame.replay_time_sec:.3f}s")
    finally:
        for sock in sockets.values():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mcap_path", type=Path)
    parser.add_argument("--source", choices=tuple(TOPICS_BY_SOURCE), default="hand_command")
    parser.add_argument("--timing", choices=("log_time", "source_time", "apply_time"), default="log_time")
    parser.add_argument("--left-host", default="127.0.0.1")
    parser.add_argument("--left-port", type=int, default=8765)
    parser.add_argument("--right-host", default="127.0.0.1")
    parser.add_argument("--right-port", type=int, default=8767)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--duration-sec", type=float)
    parser.add_argument("--max-gap-sec", type=float)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    frames = list(iter_replay_frames(args.mcap_path, source=args.source, timing=args.timing))
    scheduled = schedule_replay_frames(
        frames, start_sec=args.start_sec, duration_sec=args.duration_sec
    )
    if not scheduled:
        print("No Wuji replay frames selected.")
        return 1
    replay_scheduled_frames(scheduled, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
