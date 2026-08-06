import argparse
import signal
import socket
import time
from collections import defaultdict

import numpy as np

from qpos_protocol import encode_message, make_hello_message, make_keypoints_message, make_qpos_message
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
    stop_requested = False
    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)

    try:
        sock = open_socket(args.host, args.port, args.connect_timeout)
        if args.send_timeout > 0:
            sock.settimeout(args.send_timeout)
        print('----- hello ')
        sock.sendall(encode_message(make_hello_message(args.hand, time.time())))
        print(f"Streaming {args.stream_mode} frames to {args.host}:{args.port}")

        interval = 1.0 / args.rate
        interval_ms = interval * 1000.0
        slow_ms = args.debug_slow_ms if args.debug_slow_ms > 0 else max(50.0, 2.0 * interval_ms)
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        seq = 0
        skipped_cached = 0
        last_print = 0.0
        report_start = time.monotonic()
        last_loop_start = None
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
            if args.debug_latency:
                debug_payload = {
                    "client_glove_ms": round(glove_ms, 3),
                    "client_retarget_ms": round(retarget_ms, 3),
                    "client_glove_cache_hit": glove_debug.get("last_poll_new_frames", 0) == 0,
                    "client_glove_age_ms": glove_debug.get("last_cache_age_ms"),
                    "client_sdk_drained": glove_debug.get("last_poll_drained_frames", 0),
                }

            encode_start = time.perf_counter()
            if args.stream_mode == "qpos":
                message = make_qpos_message(
                    seq, qpos, time.time(), args.hand, debug=debug_payload
                )
            else:
                message = make_keypoints_message(seq, fingers_pose, time.time(), args.hand, debug=debug_payload)
            payload = encode_message(message)
            encode_ms = (time.perf_counter() - encode_start) * 1000.0
            stats.add("encode_ms", encode_ms)

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
                    opt_summary = optimizer_timing_summary(retargeter)
                    print(
                        f"latency-client sent={seq} fps={fps:.1f} "
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
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        cleanup_input_device(input_device)
        print("Stopped. Glove disconnected and socket closed.")


if __name__ == "__main__":
    run(parse_args())
