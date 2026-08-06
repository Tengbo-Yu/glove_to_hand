#!/usr/bin/env python3
import argparse
import signal
import socket
import time
from collections import defaultdict

import numpy as np

from qpos_protocol import (
    SocketLineReader,
    decode_message,
    encode_message,
    make_hello_message,
    make_qpos_message,
)
from retargeting_hand2 import Hand2RetargetPipeline


class IntervalStats:
    def __init__(self):
        self.samples = defaultdict(list)
        self.counters = defaultdict(float)

    def add(self, name, value):
        if value is not None:
            self.samples[name].append(float(value))

    def inc(self, name, value=1):
        self.counters[name] += value

    def avg(self, name):
        values = self.samples.get(name, ())
        return sum(values) / len(values) if values else 0.0

    def max(self, name):
        values = self.samples.get(name, ())
        return max(values) if values else 0.0

    def percentile(self, name, percentile):
        values = self.samples.get(name, ())
        if not values:
            return 0.0
        return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))

    def count(self, name):
        return len(self.samples.get(name, ()))

    def reset(self):
        self.samples.clear()
        self.counters.clear()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Receive Wuji glove keypoints, retarget on this host, and forward qpos to a robot hand server."
    )
    parser.add_argument("--bind-host", default="0.0.0.0", help="IP to listen on for RDK keypoint client.")
    parser.add_argument("--listen-port", type=int, default=8765, help="TCP port for RDK keypoint client.")
    parser.add_argument("--robot-host", required=True, help="Robot machine IP running hand_qpos_server.py.")
    parser.add_argument("--robot-port", type=int, default=8765, help="Robot qpos server TCP port.")
    parser.add_argument("--hand", default="right", choices=("left", "right"), help="Hand 2 side for retargeting.")
    parser.add_argument("--config", default=None, help="Hand 2 retargeting YAML config path.")
    parser.add_argument("--keep-listening", action="store_true", help="Accept another RDK client after one disconnects.")
    parser.add_argument("--connect-timeout", type=float, default=10.0, help="Seconds to wait when connecting to robot.")
    parser.add_argument("--socket-timeout", type=float, default=1.0, help="Seconds to wait for keypoint frames before printing a warning.")
    parser.add_argument("--print-every", type=float, default=0.5, help="Seconds between status prints.")
    parser.add_argument("--retarget-lp-alpha", type=float, default=0.0, help="Override retargeter low-pass alpha. 0 keeps config value.")
    parser.add_argument("--debug-latency", action="store_true", help="Print timing summaries.")
    return parser.parse_args()


def open_robot_socket(host, port, connect_timeout, hand_side):
    sock = socket.create_connection((host, port), timeout=connect_timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(None)
    sock.sendall(encode_message(make_hello_message(hand_side, time.time())))
    return sock


def read_latest_frame(reader, expected_hand, metrics=None):
    message = None
    dropped = 0

    def process_line(line):
        nonlocal message, dropped
        if line is None:
            return
        try:
            decoded = decode_message(line)
        except ValueError as exc:
            if metrics is not None:
                metrics["malformed"] += 1
            print(f"Ignoring malformed RDK message: {exc}", flush=True)
            return
        if decoded["type"] == "hello":
            print(f"RDK hello: hand_side={decoded.get('hand_side', 'unknown')}", flush=True)
            return
        frame_hand = decoded.get("hand_side")
        if frame_hand and frame_hand != expected_hand:
            print(f"Ignoring {frame_hand} frame on {expected_hand} bridge", flush=True)
            return
        if message is not None:
            dropped += 1
        message = decoded

    process_line(reader.read_line())
    for line in reader.read_available_lines():
        process_line(line)
    return message, dropped


def serve_connection(conn, peer, args, pipeline, stop_requested):
    print(f"RDK keypoint client connected: {peer}", flush=True)
    conn.settimeout(args.socket_timeout)
    reader = SocketLineReader(conn)
    robot_sock = None
    stats = IntervalStats()
    last_print = time.monotonic()
    report_start = last_print
    sent = 0
    timeouts = 0

    try:
        robot_sock = open_robot_socket(args.robot_host, args.robot_port, args.connect_timeout, args.hand)
        print(f"Connected robot qpos server: {args.robot_host}:{args.robot_port}", flush=True)

        while not stop_requested():
            try:
                message, dropped = read_latest_frame(reader, args.hand, stats.counters)
            except EOFError:
                print("RDK keypoint client disconnected.", flush=True)
                break

            if message is None:
                timeouts += 1
                if timeouts == 1 or timeouts % 5 == 0:
                    print(f"Waiting for RDK keypoint frames... socket_timeouts={timeouts}", flush=True)
                continue
            timeouts = 0
            stats.inc("dropped_socket", dropped)

            frame_start = time.perf_counter()
            if message["type"] == "keypoints_frame":
                retarget_start = time.perf_counter()
                qpos = pipeline.retarget(message["keypoints"]).reshape(5, 4)
                retarget_ms = (time.perf_counter() - retarget_start) * 1000.0
                stats.add("retarget_ms", retarget_ms)
            else:
                qpos = message["qpos"]
                retarget_ms = 0.0

            debug = dict(message.get("debug") or {})
            if args.debug_latency:
                debug.update({
                    "host_retarget_ms": round(retarget_ms, 3),
                    "host_age_ms": round((time.time() - message["timestamp"]) * 1000.0, 3),
                })
            payload = encode_message(
                make_qpos_message(
                    message["seq"],
                    qpos,
                    message["timestamp"],
                    args.hand,
                    debug=debug or None,
                )
            )

            send_start = time.perf_counter()
            robot_sock.sendall(payload)
            send_ms = (time.perf_counter() - send_start) * 1000.0
            frame_ms = (time.perf_counter() - frame_start) * 1000.0
            stats.add("send_ms", send_ms)
            stats.add("frame_ms", frame_ms)
            stats.add("age_ms", (time.time() - message["timestamp"]) * 1000.0)
            sent += 1

            now = time.monotonic()
            if now - last_print >= args.print_every:
                elapsed = max(now - report_start, 1e-9)
                if args.debug_latency:
                    print(
                        f"bridge hand={args.hand} sent={sent} fps={stats.count('frame_ms') / elapsed:.1f} "
                        f"retarget_ms avg/p95/max={stats.avg('retarget_ms'):.1f}/"
                        f"{stats.percentile('retarget_ms', 95):.1f}/{stats.max('retarget_ms'):.1f} "
                        f"send_ms avg/max={stats.avg('send_ms'):.1f}/{stats.max('send_ms'):.1f} "
                        f"age_ms avg/max={stats.avg('age_ms'):.1f}/{stats.max('age_ms'):.1f} "
                        f"dropped={int(stats.counters['dropped_socket'])}",
                        flush=True,
                    )
                else:
                    print(
                        f"bridge hand={args.hand} sent={sent} seq={message['seq']} "
                        f"thumb={np.round(qpos[0], 3).tolist()} index={np.round(qpos[1], 3).tolist()}",
                        flush=True,
                    )
                stats.reset()
                report_start = now
                last_print = now
    finally:
        if robot_sock is not None:
            try:
                robot_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            robot_sock.close()
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()
        print("Bridge session ended.", flush=True)


def run(args):
    pipeline = Hand2RetargetPipeline(args.config, args.hand)

    print(f"Host retarget bridge hand: {args.hand}")
    print(f"Retarget config: {pipeline.config_path}")
    print(f"Listen for RDK keypoints: {args.bind_host}:{args.listen_port}")
    print(f"Forward robot qpos: {args.robot_host}:{args.robot_port}")

    if args.retarget_lp_alpha > 0:
        pipeline.retargeter.lp_filter.alpha = args.retarget_lp_alpha
        print(f"Retarget low-pass alpha override: {args.retarget_lp_alpha}")

    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.bind_host, args.listen_port))
    server.listen(1)
    server.settimeout(0.5)

    def should_stop():
        return stop_requested

    try:
        while not stop_requested:
            try:
                conn, peer = server.accept()
            except socket.timeout:
                continue
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            serve_connection(conn, peer, args, pipeline, should_stop)
            if not args.keep_listening:
                break
            if not stop_requested:
                print("Waiting for next RDK keypoint client...", flush=True)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        server.close()
        print("Stopped host retarget bridge.", flush=True)


if __name__ == "__main__":
    run(parse_args())
