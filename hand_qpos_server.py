import argparse
import signal
import socket
import threading
import time
from collections import defaultdict

import numpy as np

from hand2_backend import WujiHand2Backend
from qpos_protocol import SocketLineReader, decode_message, encode_message, make_ack_message
from retargeting_hand2 import Hand2RetargetPipeline


class QposSmoother:
    def __init__(self, tau=0.05, max_velocity=0.0, enabled=True):
        self.tau = max(0.0, float(tau))
        self.max_velocity = max(0.0, float(max_velocity))
        self.enabled = enabled
        self.current = None
        self.target = None

    def initialize(self, qpos):
        """Start output from measured hardware state before accepting a target."""
        qpos = np.asarray(qpos, dtype=np.float64)
        if qpos.shape != (5, 4) or not np.isfinite(qpos).all():
            raise ValueError(f"Initial qpos must be finite with shape (5, 4), got {qpos.shape}")
        self.current = qpos.copy()

    def set_target(self, qpos):
        qpos = np.asarray(qpos, dtype=np.float64)
        self.target = qpos
        if self.current is None:
            self.current = qpos.copy()

    def step(self, dt):
        if self.target is None:
            return None
        if self.current is None or not self.enabled:
            self.current = self.target.copy()
            return self.current.copy()

        if self.tau <= 0.0:
            desired = self.target
        else:
            alpha = 1.0 - np.exp(-max(0.0, dt) / self.tau)
            desired = self.current + alpha * (self.target - self.current)

        if self.max_velocity > 0.0 and dt > 0.0:
            max_delta = self.max_velocity * dt
            delta = np.clip(desired - self.current, -max_delta, max_delta)
            self.current = self.current + delta
        else:
            self.current = desired.copy()
        return self.current.copy()


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

    def min(self, name):
        values = self.samples.get(name, ())
        return min(values) if values else 0.0

    def span(self, name):
        values = self.samples.get(name, ())
        return max(values) - min(values) if values else 0.0

    def last(self, name):
        values = self.samples.get(name, ())
        return values[-1] if values else 0.0

    def percentile(self, name, percentile):
        values = self.samples.get(name, ())
        if not values:
            return 0.0
        return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))

    def reset(self):
        self.samples.clear()
        self.counters.clear()


def optimizer_timing_summary(retargeter):
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
        description="Receive qpos/keypoints over TCP and drive a Wuji Hand 2."
    )
    parser.add_argument("--bind-host", default="0.0.0.0", help="Host/IP to listen on.")
    parser.add_argument("--port", type=int, default=8765, help="TCP port to listen on.")
    parser.add_argument("--hand", default="right", choices=("left", "right"), help="Hand 2 side.")
    parser.add_argument("--config", default=None, help="Hand 2 retargeting YAML for raw keypoint frames.")
    parser.add_argument("--keep-listening", action="store_true", help="Keep listening for another glove client after a session ends.")
    parser.add_argument("--enable-hand", action="store_true", help="Explicitly enable and move Wuji Hand 2.")
    parser.add_argument("--hand-sn", default="", help="Wuji Hand 2 serial number.")
    parser.add_argument("--hand-address", default="", help="Hand 2 address, e.g. 192.168.1.111:7447.")
    parser.add_argument("--hand-device-name", default="", help="Optional local wuji_sdk alias.")
    parser.add_argument("--duration", type=float, default=0.0, help="Run time in seconds after glove connects. Default 0 runs until Ctrl-C.")
    parser.add_argument("--rate", type=float, default=30.0, help="Hand homing timing rate in Hz.")
    parser.add_argument("--control-rate", type=float, default=60.0, help="Fixed hand command output rate in Hz.")
    parser.add_argument("--kp", type=float, default=3.5, help="Hand 2 MIT position gain.")
    parser.add_argument("--kd", type=float, default=0.1, help="Hand 2 MIT damping gain.")
    parser.add_argument("--current-limit", type=float, default=1.5, help="Per-joint current limit in A.")
    parser.add_argument("--enable-timeout", type=float, default=5.0)
    parser.add_argument("--smooth-tau", type=float, default=0.05, help="Output qpos smoothing time constant in seconds. Lower is faster; higher is smoother.")
    parser.add_argument("--max-joint-velocity", type=float, default=0.0, help="Optional output slew limit in rad/s. 0 disables slew limiting.")
    parser.add_argument("--disable-output-smoothing", action="store_true", help="Send each retargeted qpos directly without output smoothing/resampling.")
    parser.add_argument("--retarget-lp-alpha", type=float, default=0.0, help="Override retargeter low-pass alpha. 0 keeps config value.")
    parser.add_argument("--retarget-norm-delta", type=float, default=None, help="Override retargeter motion deadband. Lower is more responsive; omit to keep the YAML value.")
    parser.add_argument("--print-every", type=float, default=1.0, help="Seconds between status prints.")
    parser.add_argument("--socket-timeout", type=float, default=1.0, help="Seconds to wait for a glove frame before printing a timeout warning.")
    parser.add_argument("--command-timeout", type=float, default=1.0, help="Disable Hand 2 if no valid frame arrives for this many seconds. 0 disables the watchdog.")
    parser.add_argument("--debug-latency", action="store_true", help="Print socket/frame/hand-write timing summaries for latency diagnosis.")
    parser.add_argument("--debug-slow-ms", type=float, default=0.0, help="Print slow-frame details above this server processing time in ms. Default derives from --rate.")
    parser.add_argument("--home-on-shutdown", action=argparse.BooleanOptionalAction, default=False, help="Opt-in: move Hand 2 to zero before shutdown.")
    parser.add_argument("--home-duration", type=float, default=1.5, help="Seconds to spend moving to zero position before shutdown.")
    return parser.parse_args()


def read_latest_qpos(reader, metrics=None, expected_hand=None):
    """Drain all buffered lines, returning the newest qpos/keypoints message.

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
            side = decoded.get("hand_side")
            print(f"Glove client hello: hand_side={side}")
            if expected_hand is not None and side != expected_hand:
                print(f"Ignoring hello for {side}; this server controls {expected_hand}")
            return
        side = decoded.get("hand_side")
        if expected_hand is not None and side != expected_hand:
            print(f"Ignoring {side} frame on {expected_hand} Hand 2 server")
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
    for line in reader.read_available_lines():
        process_line(line)
    if metrics is not None:
        metrics["socket_drain_ms"] = (time.perf_counter() - drain_start) * 1000.0
    return message, dropped


def serve_connection(conn, peer, args, stop_requested):
    print(f"Glove client connected: {peer}")
    conn.settimeout(args.socket_timeout)
    reader = SocketLineReader(conn)

    backend = None
    receiver_stop = threading.Event()
    receiver_done = threading.Event()
    receiver_state = {
        "message": None,
        "dropped": 0,
        "metrics": None,
        "received_perf": None,
        "version": 0,
        "timeouts": 0,
        "eof": False,
    }
    receiver_lock = threading.Lock()

    def receiver_loop():
        while not receiver_stop.is_set() and not stop_requested():
            metrics = {} if args.debug_latency else None
            try:
                message, dropped = read_latest_qpos(
                    reader, metrics, expected_hand=args.hand
                )
            except EOFError:
                print("Glove client disconnected.")
                with receiver_lock:
                    receiver_state["eof"] = True
                break
            except Exception as exc:
                print(f"Glove receiver failed: {exc}")
                with receiver_lock:
                    receiver_state["eof"] = True
                break
            if message is None:
                with receiver_lock:
                    receiver_state["timeouts"] += 1
                    timeouts = receiver_state["timeouts"]
                if timeouts == 1 or timeouts % 5 == 0:
                    print(f"Waiting for glove frames... socket_timeouts={timeouts}")
                continue
            with receiver_lock:
                receiver_state["message"] = message
                receiver_state["dropped"] += dropped
                receiver_state["metrics"] = metrics
                receiver_state["received_perf"] = time.perf_counter()
                receiver_state["version"] += 1
                receiver_state["timeouts"] = 0
        receiver_done.set()

    receiver = threading.Thread(target=receiver_loop, name="qpos-receiver", daemon=True)

    try:
        # Validate the Hand 2 model and URDF->device order before any enable call.
        pipeline = Hand2RetargetPipeline(args.config, args.hand)
        retargeter = pipeline.retargeter
        if args.retarget_lp_alpha > 0:
            if args.retarget_lp_alpha > 1:
                raise ValueError("--retarget-lp-alpha must be in (0, 1]")
            retargeter.lp_filter.alpha = args.retarget_lp_alpha
            print(f"Retarget low-pass alpha override: {args.retarget_lp_alpha}")
        retarget_norm_delta = getattr(args, "retarget_norm_delta", None)
        if retarget_norm_delta is not None:
            if retarget_norm_delta < 0:
                raise ValueError("--retarget-norm-delta must be non-negative")
            retargeter.optimizer.norm_delta = retarget_norm_delta
            print(f"Retarget norm_delta override: {retarget_norm_delta}")
        set_optimizer_timing(retargeter, args.debug_latency)
        if args.debug_latency:
            reset_optimizer_timing(retargeter)
        print(f"Robot-side Hand 2 retarget config: {pipeline.config_path}")

        # Start receiving before touching hardware.  A TCP hello alone is not
        # sufficient authority to energize the hand: wait for a validated frame.
        receiver.start()

        if args.enable_hand:
            print(
                f"Waiting for the first valid {args.hand} command frame; "
                "Hand 2 remains disabled."
            )
            last_wait_print = time.monotonic()
            while not stop_requested():
                with receiver_lock:
                    first_message = receiver_state["message"]
                    receiver_eof = receiver_state["eof"]
                actionable_message = first_message is not None and (
                    first_message["type"] != "keypoints_frame"
                    or not np.allclose(first_message["keypoints"], 0)
                )
                if actionable_message:
                    print(
                        f"First valid {args.hand} frame received "
                        f"(seq={first_message['seq']}); connecting Hand 2."
                    )
                    break
                if receiver_eof:
                    print(
                        "Command client disconnected before sending a valid frame; "
                        "Hand 2 was not enabled."
                    )
                    return
                now = time.monotonic()
                if now - last_wait_print >= 5.0:
                    print("Still waiting for a valid command frame; Hand 2 is disabled.")
                    last_wait_print = now
                time.sleep(0.02)
            else:
                return

            # The analytical optimizer has a one-time cold-start cost. Run it
            # while the hand is still disabled so the first post-enable command
            # does not inherit that latency spike. The receiver keeps draining
            # newer RDK frames while this warm-up and hardware setup run.
            if first_message["type"] == "keypoints_frame":
                warmup_start = time.perf_counter()
                pipeline.retarget(first_message["keypoints"])
                print(
                    "Retargeter warmed up before Hand 2 enable "
                    f"({(time.perf_counter() - warmup_start) * 1000.0:.1f} ms)."
                )

            backend = WujiHand2Backend(
                args.hand,
                sn=args.hand_sn,
                address=args.hand_address,
                device_name=args.hand_device_name or f"wuji_hand_2_{args.hand}",
                kp=args.kp,
                kd=args.kd,
                current_limit=args.current_limit,
                enable_timeout=args.enable_timeout,
            )
            backend.enable()
            print("Hand 2 enabled and command publisher started.")
        else:
            print("Dry run: Hand 2 remains disabled; received qpos is printed only.")
        print(
            f"Output smoothing: {'off' if args.disable_output_smoothing else 'on'} "
            f"control_rate={args.control_rate:g}Hz smooth_tau={args.smooth_tau:g}s "
            f"max_joint_velocity={args.max_joint_velocity:g}"
        )

        control_interval = 1.0 / max(args.control_rate, 1.0)
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        next_tick = time.monotonic()
        last_tick = next_tick
        last_version = -1
        last_seq = None
        last_valid_frame_time = time.monotonic()
        received = 0
        last_qpos = None
        smoother = QposSmoother(
            tau=args.smooth_tau,
            max_velocity=args.max_joint_velocity,
            enabled=not args.disable_output_smoothing,
        )
        if backend is not None:
            measured_qpos = backend.current_positions().reshape(5, 4)
            smoother.initialize(measured_qpos)
            print(
                "Output initialized from measured Hand 2 joint positions; "
                "the first teleop frame will be rate-limited."
        )
        stats = IntervalStats()
        report_start = time.monotonic()
        last_print = report_start
        interval_ms = control_interval * 1000.0
        slow_ms = args.debug_slow_ms if args.debug_slow_ms > 0 else max(50.0, 2.0 * interval_ms)

        while not stop_requested() and (deadline is None or time.monotonic() < deadline):
            now = time.monotonic()
            sleep_s = next_tick - now
            if sleep_s > 0:
                time.sleep(sleep_s)
                now = time.monotonic()
            elif -sleep_s > control_interval:
                next_tick = now
            dt = max(0.0, now - last_tick)
            last_tick = now
            next_tick += control_interval

            with receiver_lock:
                eof = receiver_state["eof"]
                version = receiver_state["version"]
                message = receiver_state["message"]
                dropped = receiver_state["dropped"]
                read_metrics = receiver_state["metrics"]
                received_perf = receiver_state["received_perf"]
                receiver_state["dropped"] = 0
            if eof:
                break

            new_target = False
            seq = last_seq
            retarget_ms = 0.0
            wire_age_ms = 0.0
            server_queue_ms = 0.0
            seq_gap = 0
            if message is not None and version != last_version:
                frame_start = time.perf_counter()
                if received_perf is not None:
                    server_queue_ms = max(
                        0.0, (frame_start - received_perf) * 1000.0
                    )
                    stats.add("server_queue_ms", server_queue_ms)
                last_version = version
                last_valid_frame_time = time.monotonic()
                received += 1
                seq = message["seq"]
                seq_gap = 0 if last_seq is None else max(0, seq - last_seq - 1)
                last_seq = seq
                wire_age_ms = (time.time() - message["timestamp"]) * 1000.0
                stats.add("wire_age_ms", wire_age_ms)
                stats.inc("seq_gap", seq_gap)
                stats.inc("dropped_socket", dropped)
                if read_metrics is not None:
                    stats.add("socket_wait_ms", read_metrics.get("socket_wait_ms"))
                    stats.add("socket_drain_ms", read_metrics.get("socket_drain_ms"))
                    stats.inc("decoded_frames", read_metrics.get("decoded_frames", 0))
                    stats.inc("malformed_messages", read_metrics.get("malformed_messages", 0))

                if message["type"] == "keypoints_frame":
                    keypoints = message["keypoints"]
                    if not np.allclose(keypoints, 0):
                        retarget_start = time.perf_counter()
                        last_qpos = pipeline.retarget(keypoints).reshape(5, 4)
                        retarget_ms = (time.perf_counter() - retarget_start) * 1000.0
                        new_target = True
                else:
                    last_qpos = message["qpos"]
                    new_target = True
                if new_target:
                    smoother.set_target(last_qpos)
                stats.add("retarget_ms", retarget_ms)
                frame_ms = (time.perf_counter() - frame_start) * 1000.0
                stats.add("server_frame_ms", frame_ms)
                debug_payload = message.get("debug") or {}
                if args.debug_latency and debug_payload.get("request_ack"):
                    client_probe_perf = debug_payload.get("client_probe_perf")
                    if client_probe_perf is not None:
                        ack_start = time.perf_counter()
                        try:
                            conn.sendall(
                                encode_message(
                                    make_ack_message(
                                        seq,
                                        client_probe_perf,
                                        server_queue_ms,
                                        frame_ms,
                                        retarget_ms,
                                    )
                                )
                            )
                            stats.add(
                                "ack_send_ms",
                                (time.perf_counter() - ack_start) * 1000.0,
                            )
                        except (OSError, ValueError) as exc:
                            print(f"Latency ACK failed: {exc}")
                if args.debug_latency and frame_ms >= slow_ms:
                    print(
                        f"slow-server seq={seq} frame_ms={frame_ms:.1f} "
                        f"wire_age_ms={wire_age_ms:.1f} "
                        f"retarget_ms={retarget_ms:.1f} "
                        f"server_queue_ms={server_queue_ms:.1f} "
                        f"socket_wait_ms={(read_metrics or {}).get('socket_wait_ms', 0.0):.1f} "
                        f"drain_ms={(read_metrics or {}).get('socket_drain_ms', 0.0):.1f} "
                        f"dropped_in_read={dropped} seq_gap={seq_gap}",
                        flush=True,
                    )

            command_qpos = smoother.step(dt)
            hand_write_ms = 0.0
            if (
                backend is not None
                and args.command_timeout > 0
                and time.monotonic() - last_valid_frame_time > args.command_timeout
            ):
                raise TimeoutError(
                    f"No valid {args.hand} command frame for "
                    f"{args.command_timeout:g}s; disabling Hand 2"
                )
            if command_qpos is not None and backend is not None:
                hand_start = time.perf_counter()
                backend.send(command_qpos.reshape(-1))
                hand_write_ms = (time.perf_counter() - hand_start) * 1000.0
                if args.debug_latency:
                    stats.add("thumb_j4_target", command_qpos[0, 3])
                    feedback = backend.latest_positions()
                    if feedback is not None:
                        stats.add("thumb_j4_feedback", feedback[3])
            stats.add("hand_write_ms", hand_write_ms)
            stats.inc("control_ticks")

            now = time.monotonic()
            if now - last_print >= args.print_every and command_qpos is not None:
                if args.debug_latency:
                    elapsed = max(now - report_start, 1e-9)
                    recv_fps = stats.count("server_frame_ms") / elapsed
                    control_fps = stats.counters["control_ticks"] / elapsed
                    opt_summary = optimizer_timing_summary(retargeter)
                    print(
                        f"latency-server recv={received} recv_fps={recv_fps:.1f} "
                        f"control_fps={control_fps:.1f} seq={seq} "
                        f"clock_wire_age_ms avg/p95/max={stats.avg('wire_age_ms'):.1f}/"
                        f"{stats.percentile('wire_age_ms', 95):.1f}/{stats.max('wire_age_ms'):.1f} "
                        f"server_queue_ms avg/p95/max={stats.avg('server_queue_ms'):.1f}/"
                        f"{stats.percentile('server_queue_ms', 95):.1f}/"
                        f"{stats.max('server_queue_ms'):.1f} "
                        f"retarget_ms avg/p95/max={stats.avg('retarget_ms'):.1f}/"
                        f"{stats.percentile('retarget_ms', 95):.1f}/{stats.max('retarget_ms'):.1f} "
                        f"socket_wait_ms avg/max={stats.avg('socket_wait_ms'):.1f}/{stats.max('socket_wait_ms'):.1f} "
                        f"drain_ms avg/max={stats.avg('socket_drain_ms'):.1f}/{stats.max('socket_drain_ms'):.1f} "
                        f"hand_write_ms avg/p95/max={stats.avg('hand_write_ms'):.1f}/"
                        f"{stats.percentile('hand_write_ms', 95):.1f}/{stats.max('hand_write_ms'):.1f} "
                        f"ack_send_ms avg/max={stats.avg('ack_send_ms'):.2f}/"
                        f"{stats.max('ack_send_ms'):.2f} "
                        f"dropped_socket={int(stats.counters['dropped_socket'])} "
                        f"seq_gap={int(stats.counters['seq_gap'])} "
                        f"thumb_J4 target/span={stats.last('thumb_j4_target'):.3f}/"
                        f"{stats.span('thumb_j4_target'):.3f} "
                        f"feedback/span={stats.last('thumb_j4_feedback'):.3f}/"
                        f"{stats.span('thumb_j4_feedback'):.3f} "
                        f"thumb={np.round(command_qpos[0], 3).tolist()} "
                        f"index={np.round(command_qpos[1], 3).tolist()} "
                        f"{opt_summary}",
                        flush=True,
                    )
                    stats.reset()
                    report_start = now
                    reset_optimizer_timing(retargeter)
                else:
                    print(
                        f"recv={received} seq={seq} "
                        f"thumb={np.round(command_qpos[0], 3).tolist()} "
                        f"index={np.round(command_qpos[1], 3).tolist()}"
                    )
                last_print = now
    finally:
        receiver_stop.set()
        if receiver.is_alive():
            receiver.join(timeout=1.0)
        if backend is not None:
            if backend.is_enabled and args.home_on_shutdown:
                try:
                    print(f"Homing Hand 2 to zero for {args.home_duration:.1f}s before shutdown.")
                    backend.home(args.home_duration, args.rate)
                except Exception as exc:
                    print(f"Shutdown homing skipped: {exc}")
            backend.close()
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()
        print("Session ended. Hand 2 disabled and socket closed.")


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
    print(f"Mode: {'MOVE HAND 2' if args.enable_hand else 'DRY RUN'}")

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
