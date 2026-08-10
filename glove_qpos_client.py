import argparse
import os
import select
import signal
import socket
import threading
import time
from collections import defaultdict

import numpy as np

from data_collector_telemetry import (
    DataCollectorTelemetryPublisher,
    build_glove_command_payload,
    default_endpoint,
    default_source,
    seconds_to_ns,
)
from qpos_protocol import (
    PROTOCOL_NAME,
    decode_message,
    encode_message,
    make_hello_message,
    make_keypoints_message,
    make_qpos_message,
)
from retargeting_hand2 import Hand2RetargetPipeline
from wuji_glove_input import WujiGloveDevice


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


def optimizer_timing_summary(retargeter):
    if retargeter is None:
        return ""
    optimizer = getattr(retargeter, "optimizer", None)
    if optimizer is None or not hasattr(optimizer, "get_timing_stats"):
        return ""
    timing = optimizer.get_timing_stats()
    avg = timing.get_avg()
    iter_stats = timing.get_iter_stats()
    parts = []
    if avg.get("call_count", 0):
        parts.append(
            "opt_ms avg_total={:.2f} nlopt={:.2f} fk={:.2f} jac={:.2f} grad={:.2f}".format(
                avg.get("total_ms", 0.0),
                avg.get("nlopt_ms", 0.0),
                avg.get("fk_ms", 0.0),
                avg.get("jacobian_ms", 0.0),
                avg.get("gradient_ms", 0.0),
            )
        )
    if iter_stats:
        parts.append(
            "opt_iters mean={:.1f} p90={:.1f} max={}".format(
                iter_stats.get("mean", 0.0),
                iter_stats.get("p90", 0.0),
                iter_stats.get("max", 0),
            )
        )
    return " ".join(parts)


def reset_optimizer_timing(retargeter):
    optimizer = getattr(retargeter, "optimizer", None)
    if optimizer is not None and hasattr(optimizer, "reset_timing_stats"):
        optimizer.reset_timing_stats()


def set_optimizer_timing(retargeter, enabled):
    optimizer = getattr(retargeter, "optimizer", None)
    if optimizer is not None and hasattr(optimizer, "set_timing_enabled"):
        optimizer.set_timing_enabled(enabled)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Read a Wuji Glove, retarget to hand joints, and stream qpos to a hand server over TCP."
    )
    parser.add_argument("--host", default="192.168.123.164", help="hand_qpos_server host/IP.")
    parser.add_argument("--port", type=int, default=8765, help="hand_qpos_server TCP port.")
    parser.add_argument("--hand", default="right", choices=("left", "right"), help="Glove/Hand 2 side.")
    parser.add_argument("--glove-sn", default="", help="Wuji Glove serial number. Use when multiple Wuji devices are online.")
    parser.add_argument("--device-name", default="glove", help="wuji_sdk device name for Wuji Glove.")
    parser.add_argument("--config", default=None, help="Hand 2 retargeting YAML. Used only for --stream-mode=qpos.")
    parser.add_argument("--stream-mode", choices=("keypoints", "qpos"), default="keypoints", help="Send raw glove keypoints for robot-side retargeting, or retarget locally and send qpos.")
    parser.add_argument("--glove-stream", choices=("hand_skeleton", "offline_hand_skeleton"), default="hand_skeleton", help="Wuji SDK stream for keypoint input. hand_skeleton matches the official teleop path.")
    parser.add_argument("--emf-rate-divider", type=int, default=0, help="Set the glove EMF/derived-stream rate divider. 1 is full rate (~120 Hz); 0 keeps the device value.")
    parser.add_argument("--wuji-log-level", default="error", choices=("trace", "debug", "info", "warn", "warning", "error", "off"), help="wuji_sdk internal log level.")
    parser.add_argument("--duration", type=float, default=0.0, help="Run time in seconds. Default 0 runs until Ctrl-C.")
    parser.add_argument("--rate", type=float, default=30.0, help="Frame send rate in Hz.")
    parser.add_argument("--connect-timeout", type=float, default=10.0, help="Seconds to wait when connecting to the hand server.")
    parser.add_argument("--print-every", type=float, default=1.0, help="Seconds between status prints.")
    parser.add_argument("--debug-latency", action="store_true", help="Print per-stage timing summaries for latency diagnosis.")
    parser.add_argument("--debug-slow-ms", type=float, default=0.0, help="Print slow-frame details above this work time in ms. Default derives from --rate.")
    parser.add_argument("--print-qpos", action="store_true", help="Print every qpos matrix. This can block stdout and add latency.")
    parser.add_argument("--send-timeout", type=float, default=0.0, help="Optional socket send timeout in seconds after connect. Default 0 keeps blocking sends.")
    parser.add_argument("--skip-cached-frames", action="store_true", help="Skip cached glove frames when no fresh SDK frame is available. This can reduce stale commands but may starve the robot if the SDK produces fresh frames slowly.")
    parser.add_argument("--telemetry", action=argparse.BooleanOptionalAction, default=True, help="Publish glove input telemetry to Hammerhead DataCollector.")
    parser.add_argument("--telemetry-host", default=os.environ.get("DATA_COLLECTOR_HOST", "127.0.0.1"), help="DataCollector host for the default side-specific endpoint.")
    parser.add_argument("--telemetry-endpoint", default=None, help="Explicit glove telemetry endpoint; overrides --telemetry-host.")
    return parser.parse_args()


def cleanup_input_device(input_device):
    for method_name in ("stop", "cleanup", "close"):
        method = getattr(input_device, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass


def open_socket(host, port, connect_timeout):
    sock = socket.create_connection((host, port), timeout=connect_timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(None)
    return sock


def create_glove_telemetry_publisher(args):
    if not args.telemetry:
        return None
    source = default_source("glove_command", args.hand)
    return DataCollectorTelemetryPublisher(
        endpoint=args.telemetry_endpoint
        or default_endpoint("glove_command", args.hand, args.telemetry_host),
        source=source,
        frame_id=source,
    )


def receive_latency_acks(sock, stop_event, stats, stats_lock):
    """Receive optional server ACKs without interfering with frame sending."""
    buffer = b""
    while not stop_event.is_set():
        try:
            readable, _, _ = select.select([sock], [], [], 0.2)
            if not readable:
                continue
            chunk = sock.recv(65536)
            if not chunk:
                return
            buffer += chunk
        except (OSError, socket.timeout, ValueError):
            if not stop_event.is_set():
                with stats_lock:
                    stats.inc("ack_recv_errors")
            return

        while b"\n" in buffer:
            line, _, buffer = buffer.partition(b"\n")
            try:
                message = decode_message(line)
                if message["type"] != "ack":
                    continue
                rtt_ms = max(
                    0.0,
                    (time.perf_counter() - message["client_probe_perf"]) * 1000.0,
                )
                # The residual is the round-trip transport, client encode/
                # receive, and server ACK-send cost after removing server-local
                # queue + frame processing. It is intentionally not called
                # one-way network latency.
                transport_residual_ms = max(
                    0.0,
                    rtt_ms
                    - message["server_queue_ms"]
                    - message["server_frame_ms"],
                )
                with stats_lock:
                    stats.add("ack_rtt_ms", rtt_ms)
                    stats.add("ack_server_queue_ms", message["server_queue_ms"])
                    stats.add("ack_server_frame_ms", message["server_frame_ms"])
                    stats.add("ack_server_retarget_ms", message["server_retarget_ms"])
                    stats.add("ack_transport_residual_ms", transport_residual_ms)
            except (KeyError, TypeError, ValueError):
                with stats_lock:
                    stats.inc("ack_decode_errors")


def run(args):
    pipeline = (
        Hand2RetargetPipeline(args.config, args.hand)
        if args.stream_mode == "qpos"
        else None
    )
    if pipeline is not None:
        print(f"Config: {pipeline.config_path}")
    else:
        print("Config: robot-side retargeting (--stream-mode=keypoints)")
    print(f"Stream mode: {args.stream_mode}")
    print(f"Hand side: {args.hand}")
    print(f"Glove device name: {args.device_name}")
    print(f"Glove stream: {args.glove_stream}")
    print(f"Wuji SDK log level: {args.wuji_log_level}")
    print(f"Hand server: {args.host}:{args.port}")

    input_device = WujiGloveDevice(
        hand_side=args.hand,
        device_name=args.device_name,
        sn=args.glove_sn or None,
        stream=args.glove_stream,
        sdk_log_level=args.wuji_log_level,
        emf_rate_divider=args.emf_rate_divider or None,
    )
    retargeter = pipeline.retargeter if pipeline is not None else None

    sock = None
    telemetry = None
    ack_stop = threading.Event()
    ack_receiver = None
    ack_stats = IntervalStats()
    ack_stats_lock = threading.Lock()
    stop_requested = False
    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)

    try:
        telemetry = create_glove_telemetry_publisher(args)
        if telemetry is not None:
            print(f"Glove telemetry: {telemetry.source} -> {telemetry.endpoint}")
        sock = open_socket(args.host, args.port, args.connect_timeout)
        if args.send_timeout > 0:
            sock.settimeout(args.send_timeout)
        print('----- hello ')
        sock.sendall(encode_message(make_hello_message(args.hand, time.time())))
        print(f"Streaming {args.stream_mode} frames to {args.host}:{args.port}")
        if args.debug_latency:
            ack_receiver = threading.Thread(
                target=receive_latency_acks,
                args=(sock, ack_stop, ack_stats, ack_stats_lock),
                name="latency-ack-receiver",
                daemon=True,
            )
            ack_receiver.start()

        interval = 1.0 / args.rate
        interval_ms = interval * 1000.0
        slow_ms = args.debug_slow_ms if args.debug_slow_ms > 0 else max(50.0, 2.0 * interval_ms)
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        seq = 0
        skipped_cached = 0
        report_start = time.monotonic()
        last_print = report_start
        last_loop_start = None
        last_fresh_frame = None
        next_tick = time.monotonic()
        stats = IntervalStats()
        set_optimizer_timing(retargeter, args.debug_latency)
        if args.debug_latency:
            reset_optimizer_timing(retargeter)

        while not stop_requested and (deadline is None or time.monotonic() < deadline):
            loop_start = time.perf_counter()
            if last_loop_start is not None:
                stats.add("loop_period_ms", (loop_start - last_loop_start) * 1000.0)
            last_loop_start = loop_start

            glove_start = time.perf_counter()
            fingers_data = input_device.get_fingers_data()
            glove_ms = (time.perf_counter() - glove_start) * 1000.0
            fingers_pose = fingers_data[f"{args.hand}_fingers"]
            stats.add("glove_ms", glove_ms)

            glove_debug = input_device.get_debug_stats() if hasattr(input_device, "get_debug_stats") else {}
            got_fresh_frame = glove_debug.get("last_poll_new_frames", 1) > 0
            if got_fresh_frame:
                fresh_now = time.perf_counter()
                stats.inc("fresh_frames")
                if last_fresh_frame is not None:
                    stats.add(
                        "fresh_period_ms",
                        (fresh_now - last_fresh_frame) * 1000.0,
                    )
                last_fresh_frame = fresh_now
            if args.debug_latency and glove_debug:
                stats.inc("cache_hits", 1 if not got_fresh_frame else 0)
                stats.inc("sdk_drained", glove_debug.get("last_poll_drained_frames", 0))
                stats.add("glove_age_ms", glove_debug.get("last_cache_age_ms"))
                stats.add("sdk_recv_ms", glove_debug.get("last_recv_ms"))
                stats.add("sdk_drain_ms", glove_debug.get("last_drain_ms"))

            if fingers_pose is None or np.allclose(fingers_pose, 0):
                time.sleep(0.01)
                continue
            if not got_fresh_frame and args.skip_cached_frames:
                skipped_cached += 1
                stats.inc("skipped_cached")
                now = time.monotonic()
                if now - last_print >= args.print_every:
                    if args.debug_latency:
                        elapsed = max(now - report_start, 1e-9)
                        print(
                            f"latency-client sent={seq} fps={stats.count('work_ms') / elapsed:.1f} "
                            f"skipped_cached={int(stats.counters['skipped_cached'])} "
                            f"cache_hits={int(stats.counters['cache_hits'])} "
                            f"glove_age_ms max={stats.max('glove_age_ms'):.1f} "
                            f"sdk_drained={int(stats.counters['sdk_drained'])}",
                            flush=True,
                        )
                        stats.reset()
                        report_start = now
                    else:
                        print(f"sent={seq} skipped_cached={skipped_cached}")
                    last_print = now
                time.sleep(0.001)
                continue

            retarget_ms = 0.0
            qpos = None
            if args.stream_mode == "qpos":
                retarget_start = time.perf_counter()
                qpos = pipeline.retarget(fingers_pose).reshape(5, 4)
                retarget_ms = (time.perf_counter() - retarget_start) * 1000.0
                stats.add("retarget_ms", retarget_ms)
                if args.print_qpos:
                    print(qpos)

            debug_payload = None
            client_probe_perf = None
            if args.debug_latency:
                client_probe_perf = time.perf_counter()
                debug_payload = {
                    "client_glove_ms": round(glove_ms, 3),
                    "client_retarget_ms": round(retarget_ms, 3),
                    "client_glove_cache_hit": glove_debug.get("last_poll_new_frames", 0) == 0,
                    "client_glove_age_ms": glove_debug.get("last_cache_age_ms"),
                    "client_sdk_drained": glove_debug.get("last_poll_drained_frames", 0),
                    "request_ack": True,
                    "client_probe_perf": client_probe_perf,
                }

            encode_start = time.perf_counter()
            frame_timestamp_s = time.time()
            if args.stream_mode == "qpos":
                message = make_qpos_message(
                    seq, qpos, frame_timestamp_s, args.hand, debug=debug_payload
                )
            else:
                message = make_keypoints_message(
                    seq, fingers_pose, frame_timestamp_s, args.hand, debug=debug_payload
                )
            payload = encode_message(message)
            encode_ms = (time.perf_counter() - encode_start) * 1000.0
            stats.add("encode_ms", encode_ms)

            if telemetry is not None:
                telemetry.publish(
                    sequence=seq,
                    source_timestamp_ns=seconds_to_ns(frame_timestamp_s),
                    payload=build_glove_command_payload(
                        hand_side=args.hand,
                        seq=seq,
                        keypoints=fingers_pose,
                        stream_mode=args.stream_mode,
                        retargeted_qpos=qpos,
                        retarget_config=(
                            str(pipeline.config_path) if pipeline is not None else None
                        ),
                        glove_device_name=args.device_name,
                        glove_sn=args.glove_sn,
                        glove_stream=args.glove_stream,
                        tcp_target=f"{args.host}:{args.port}",
                        protocol=PROTOCOL_NAME,
                        cached_frame=not got_fresh_frame,
                        telemetry_dropped_count=telemetry.dropped_count,
                    ),
                )

            send_start = time.perf_counter()
            sock.sendall(payload)
            sendall_ms = (time.perf_counter() - send_start) * 1000.0
            stats.add("sendall_ms", sendall_ms)

            work_ms = (time.perf_counter() - loop_start) * 1000.0
            behind_ms = max(0.0, work_ms - interval_ms)
            stats.add("work_ms", work_ms)
            stats.add("behind_ms", behind_ms)
            if args.debug_latency and work_ms >= slow_ms:
                print(
                    f"slow-client seq={seq} work_ms={work_ms:.1f} "
                    f"glove_ms={glove_ms:.1f} retarget_ms={retarget_ms:.1f} "
                    f"encode_ms={encode_ms:.1f} sendall_ms={sendall_ms:.1f} "
                    f"behind_ms={behind_ms:.1f} "
                    f"glove_age_ms={glove_debug.get('last_cache_age_ms')}",
                    flush=True,
                )
            seq += 1

            now = time.monotonic()
            if now - last_print >= args.print_every:
                if args.debug_latency:
                    elapsed = max(now - report_start, 1e-9)
                    fps = stats.count("work_ms") / elapsed
                    fresh_fps = stats.counters["fresh_frames"] / elapsed
                    opt_summary = optimizer_timing_summary(retargeter)
                    with ack_stats_lock:
                        ack_count = ack_stats.count("ack_rtt_ms")
                        ack_summary = (
                            f"ack={ack_count} "
                            f"app_rtt_ms avg/p95/max={ack_stats.avg('ack_rtt_ms'):.1f}/"
                            f"{ack_stats.percentile('ack_rtt_ms', 95):.1f}/"
                            f"{ack_stats.max('ack_rtt_ms'):.1f} "
                            f"server_queue_ms p95/max="
                            f"{ack_stats.percentile('ack_server_queue_ms', 95):.1f}/"
                            f"{ack_stats.max('ack_server_queue_ms'):.1f} "
                            f"server_retarget_ms p95/max="
                            f"{ack_stats.percentile('ack_server_retarget_ms', 95):.1f}/"
                            f"{ack_stats.max('ack_server_retarget_ms'):.1f} "
                            f"transport_rtt_residual_ms p95/max="
                            f"{ack_stats.percentile('ack_transport_residual_ms', 95):.1f}/"
                            f"{ack_stats.max('ack_transport_residual_ms'):.1f}"
                        )
                        ack_stats.reset()
                    print(
                        f"latency-client sent={seq} fps={fps:.1f} "
                        f"fresh_fps={fresh_fps:.1f} "
                        f"fresh_period_ms p95/max={stats.percentile('fresh_period_ms', 95):.1f}/"
                        f"{stats.max('fresh_period_ms'):.1f} "
                        f"loop_ms avg/p95/max={stats.avg('loop_period_ms'):.1f}/"
                        f"{stats.percentile('loop_period_ms', 95):.1f}/{stats.max('loop_period_ms'):.1f} "
                        f"glove_ms avg/max={stats.avg('glove_ms'):.1f}/{stats.max('glove_ms'):.1f} "
                        f"retarget_ms avg/p95/max={stats.avg('retarget_ms'):.1f}/"
                        f"{stats.percentile('retarget_ms', 95):.1f}/{stats.max('retarget_ms'):.1f} "
                        f"sendall_ms avg/max={stats.avg('sendall_ms'):.1f}/{stats.max('sendall_ms'):.1f} "
                        f"work_ms avg/p95/max={stats.avg('work_ms'):.1f}/"
                        f"{stats.percentile('work_ms', 95):.1f}/{stats.max('work_ms'):.1f} "
                        f"behind_ms max={stats.max('behind_ms'):.1f} "
                        f"cache_hits={int(stats.counters['cache_hits'])} "
                        f"glove_age_ms max={stats.max('glove_age_ms'):.1f} "
                        f"sdk_drained={int(stats.counters['sdk_drained'])} "
                        f"{ack_summary} "
                        f"{opt_summary}",
                        flush=True,
                    )
                    stats.reset()
                    report_start = now
                    reset_optimizer_timing(retargeter)
                else:
                    if qpos is not None:
                        print(
                            f"sent={seq} "
                            f"thumb={np.round(qpos[0], 3).tolist()} "
                            f"index={np.round(qpos[1], 3).tolist()}"
                        )
                    else:
                        print(
                            f"sent={seq} keypoints "
                            f"wrist={np.round(fingers_pose[0], 3).tolist()} "
                            f"index_tip={np.round(fingers_pose[8], 3).tolist()}"
                        )
                last_print = now

            now = time.monotonic()
            sleep_s = next_tick - now
            if sleep_s > 0:
                time.sleep(sleep_s)
            elif -sleep_s > interval:
                next_tick = now
            next_tick += interval
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if sock is not None:
            ack_stop.set()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if ack_receiver is not None:
                ack_receiver.join(timeout=0.5)
            sock.close()
        if telemetry is not None:
            telemetry.close()
        cleanup_input_device(input_device)
        print("Stopped. Glove disconnected and socket closed.")


if __name__ == "__main__":
    run(parse_args())
