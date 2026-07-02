import argparse
import signal
import socket
import time
from collections import defaultdict

import numpy as np
import wujihandpy

from qpos_protocol import SocketLineReader, decode_message


class IntervalStats:
    def __init__(self):
        self.samples = defaultdict(list)
        self.counters = defaultdict(float)

    def add(self, name, value):
        if value is None:
            return
        self.samples[name].append(float(value))

    def inc(self, name, value=1):
        self.counters[name] += value

    def count(self, name):
        return len(self.samples.get(name, ()))

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

    def reset(self):
        self.samples.clear()
        self.counters.clear()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Receive retargeted qpos over TCP and drive a Wuji Hand."
    )
    parser.add_argument("--bind-host", default="0.0.0.0", help="Host/IP to listen on.")
    parser.add_argument("--port", type=int, default=8765, help="TCP port to listen on.")
    parser.add_argument("--keep-listening", action="store_true", help="Keep listening for another glove client after a session ends.")
    parser.add_argument("--enable-hand", action="store_true", help="Actually enable and move Wuji Hand.")
    parser.add_argument("--hand-serial", default=None, help="USB serial number for Wuji Hand when multiple hands are connected.")
    parser.add_argument("--duration", type=float, default=0.0, help="Run time in seconds after glove connects. Default 0 runs until Ctrl-C.")
    parser.add_argument("--rate", type=float, default=30.0, help="Hand homing timing rate in Hz.")
    parser.add_argument("--lowpass", type=float, default=5.0, help="Wuji Hand realtime low-pass cutoff in Hz.")
    parser.add_argument("--print-every", type=float, default=1.0, help="Seconds between status prints.")
    parser.add_argument("--socket-timeout", type=float, default=1.0, help="Seconds to wait for a glove frame before printing a timeout warning.")
    parser.add_argument("--debug-latency", action="store_true", help="Print socket/frame/hand-write timing summaries for latency diagnosis.")
    parser.add_argument("--debug-slow-ms", type=float, default=0.0, help="Print slow-frame details above this server processing time in ms. Default derives from --rate.")
    parser.add_argument("--home-on-shutdown", action=argparse.BooleanOptionalAction, default=True, help="Move Wuji Hand to zero position before shutdown.")
    parser.add_argument("--home-duration", type=float, default=1.5, help="Seconds to spend moving to zero position before shutdown.")
    return parser.parse_args()


def home_hand(controller, duration, rate):
    start = controller.get_joint_actual_position().astype(np.float64)
    zero = np.zeros((5, 4), dtype=np.float64)
    interval = 1.0 / rate
    steps = max(1, int(duration * rate))
    for step in range(steps):
        alpha = (step + 1) / steps
        target = (1.0 - alpha) * start + alpha * zero
        controller.set_joint_target_position(target)
        time.sleep(interval)
    controller.set_joint_target_position(zero)


def create_hand_controller(hand_serial, lowpass):
    hand = wujihandpy.Hand(serial_number=hand_serial) if hand_serial else wujihandpy.Hand()
    hand.write_joint_enabled(True)
    controller = hand.realtime_controller(
        enable_upstream=False,
        filter=wujihandpy.filter.LowPass(cutoff_freq=lowpass),
    )
    return hand, controller


def read_latest_qpos(reader, metrics=None):
    """Drain all buffered lines, returning the newest qpos message.

    Returns (message_or_None, dropped). None means no full frame was available
    before the socket timeout (so the caller should keep waiting).
    """
    message = None
    dropped = 0
    if metrics is not None:
        metrics.setdefault("decoded_frames", 0)
        metrics.setdefault("malformed_messages", 0)
        metrics.setdefault("hello_messages", 0)

    def process_line(line):
        nonlocal message, dropped
        if line is None:
            return
        try:
            decoded = decode_message(line)
        except ValueError as exc:
            if metrics is not None:
                metrics["malformed_messages"] += 1
            print(f"Ignoring malformed socket message: {exc}")
            return
        if decoded["type"] == "hello":
            if metrics is not None:
                metrics["hello_messages"] += 1
            print(f"Glove client hello: hand_side={decoded.get('hand_side', 'unknown')}")
            return
        if metrics is not None:
            metrics["decoded_frames"] += 1
        if message is not None:
            dropped += 1
        message = decoded

    wait_start = time.perf_counter()
    process_line(reader.read_line())
    if metrics is not None:
        metrics["socket_wait_ms"] = (time.perf_counter() - wait_start) * 1000.0

    drain_start = time.perf_counter()
    while True:
        line = reader.read_available_line()
        if line is None:
            break
        process_line(line)
    if metrics is not None:
        metrics["socket_drain_ms"] = (time.perf_counter() - drain_start) * 1000.0
    return message, dropped


def serve_connection(conn, peer, args, stop_requested):
    print(f"Glove client connected: {peer}")
    conn.settimeout(args.socket_timeout)
    reader = SocketLineReader(conn)

    hand = None
    controller = None

    try:
        if args.enable_hand:
            hand, controller = create_hand_controller(args.hand_serial, args.lowpass)
            time.sleep(0.5)
            print("Hand enabled and realtime controller started.")
        else:
            print("Dry run: printing received qpos only. Add --enable-hand to move the hand.")

        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        received = 0
        dropped_socket = 0
        timeouts = 0
        last_print = 0.0
        last_qpos = None
        last_seq = None
        report_start = time.monotonic()
        stats = IntervalStats()
        interval_ms = (1.0 / args.rate) * 1000.0 if args.rate > 0 else 0.0
        slow_ms = args.debug_slow_ms if args.debug_slow_ms > 0 else max(50.0, 2.0 * interval_ms)

        while not stop_requested() and (deadline is None or time.monotonic() < deadline):
            frame_start = time.perf_counter()
            read_metrics = {} if args.debug_latency else None
            try:
                message, dropped = read_latest_qpos(reader, read_metrics)
            except EOFError:
                print("Glove client disconnected.")
                break

            if args.debug_latency and read_metrics is not None:
                stats.add("socket_wait_ms", read_metrics.get("socket_wait_ms"))
                stats.add("socket_drain_ms", read_metrics.get("socket_drain_ms"))
                stats.inc("decoded_frames", read_metrics.get("decoded_frames", 0))
                stats.inc("malformed_messages", read_metrics.get("malformed_messages", 0))

            if message is None:
                timeouts += 1
                if timeouts == 1 or timeouts % 5 == 0:
                    print(f"Waiting for glove frames... socket_timeouts={timeouts}")
                continue

            timeouts = 0
            received += 1
            dropped_socket += dropped
            last_qpos = message["qpos"]
            seq = message["seq"]
            seq_gap = 0 if last_seq is None else max(0, seq - last_seq - 1)
            last_seq = seq
            wire_age_ms = (time.time() - message["timestamp"]) * 1000.0
            stats.add("wire_age_ms", wire_age_ms)
            stats.inc("seq_gap", seq_gap)
            stats.inc("dropped_socket", dropped)

            hand_write_ms = 0.0
            if controller is not None:
                hand_start = time.perf_counter()
                controller.set_joint_target_position(last_qpos)
                hand_write_ms = (time.perf_counter() - hand_start) * 1000.0
            stats.add("hand_write_ms", hand_write_ms)

            frame_ms = (time.perf_counter() - frame_start) * 1000.0
            stats.add("server_frame_ms", frame_ms)
            if args.debug_latency and frame_ms >= slow_ms:
                print(
                    f"slow-server seq={seq} frame_ms={frame_ms:.1f} "
                    f"wire_age_ms={wire_age_ms:.1f} "
                    f"socket_wait_ms={(read_metrics or {}).get('socket_wait_ms', 0.0):.1f} "
                    f"drain_ms={(read_metrics or {}).get('socket_drain_ms', 0.0):.1f} "
                    f"hand_write_ms={hand_write_ms:.1f} "
                    f"dropped_in_read={dropped} seq_gap={seq_gap}",
                    flush=True,
                )

            now = time.monotonic()
            if now - last_print >= args.print_every:
                if args.debug_latency:
                    elapsed = max(now - report_start, 1e-9)
                    fps = stats.count("server_frame_ms") / elapsed
                    print(
                        f"latency-server recv={received} fps={fps:.1f} seq={seq} "
                        f"wire_age_ms avg/p95/max={stats.avg('wire_age_ms'):.1f}/"
                        f"{stats.percentile('wire_age_ms', 95):.1f}/{stats.max('wire_age_ms'):.1f} "
                        f"socket_wait_ms avg/max={stats.avg('socket_wait_ms'):.1f}/{stats.max('socket_wait_ms'):.1f} "
                        f"drain_ms avg/max={stats.avg('socket_drain_ms'):.1f}/{stats.max('socket_drain_ms'):.1f} "
                        f"hand_write_ms avg/p95/max={stats.avg('hand_write_ms'):.1f}/"
                        f"{stats.percentile('hand_write_ms', 95):.1f}/{stats.max('hand_write_ms'):.1f} "
                        f"frame_ms avg/p95/max={stats.avg('server_frame_ms'):.1f}/"
                        f"{stats.percentile('server_frame_ms', 95):.1f}/{stats.max('server_frame_ms'):.1f} "
                        f"dropped_socket={int(stats.counters['dropped_socket'])} "
                        f"seq_gap={int(stats.counters['seq_gap'])} "
                        f"thumb={np.round(last_qpos[0], 3).tolist()} "
                        f"index={np.round(last_qpos[1], 3).tolist()}",
                        flush=True,
                    )
                    stats.reset()
                    report_start = now
                else:
                    print(
                        f"recv={received} seq={message['seq']} "
                        f"thumb={np.round(last_qpos[0], 3).tolist()} "
                        f"index={np.round(last_qpos[1], 3).tolist()} "
                        f"dropped_socket={dropped_socket}"
                    )
                    dropped_socket = 0
                last_print = now
    finally:
        if hand is not None:
            if controller is not None and args.home_on_shutdown:
                try:
                    print(f"Homing hand to zero for {args.home_duration:.1f}s before shutdown.")
                    home_hand(controller, args.home_duration, args.rate)
                except Exception as exc:
                    print(f"Shutdown homing skipped: {exc}")
            hand.write_joint_enabled(False)
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()
        print("Session ended. Hand disabled and socket closed.")


def run(args):
    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.bind_host, args.port))
    server.listen(1)
    server.settimeout(0.5)
    print(f"Hand server listening on {args.bind_host}:{args.port}")
    print(f"Mode: {'MOVE HAND' if args.enable_hand else 'DRY RUN'}")

    def get_stop_requested():
        return stop_requested

    try:
        while not stop_requested:
            try:
                conn, peer = server.accept()
            except socket.timeout:
                continue

            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            serve_connection(conn, peer, args, get_stop_requested)

            if not args.keep_listening:
                break
            if not stop_requested:
                print("Waiting for the next glove client...")
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        server.close()
        print("Stopped. Server socket closed.")


if __name__ == "__main__":
    run(parse_args())
