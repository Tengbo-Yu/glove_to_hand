import argparse
import os
import signal
import socket
import sys
import threading
import time
from collections import defaultdict

import numpy as np

from data_collector_telemetry import (
    DataCollectorTelemetryPublisher,
    FINGER_NAMES,
    JOINT_NAMES,
    JOINT_ORDER,
    default_endpoint,
    default_source,
    qpos_fields,
)
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


class LatestRetargetWorker:
    """Retarget only the newest pending frame outside the fixed-rate control loop.

    The analytical optimizer has input-dependent latency and cannot be cancelled
    safely once NLopt has started.  Keep at most one pending frame so latency is
    bounded by the current solve plus the newest solve instead of building an
    unbounded FIFO of stale glove poses.
    """

    def __init__(self, pipeline, *, debug_timing=False):
        self.pipeline = pipeline
        self.debug_timing = bool(debug_timing)
        self._condition = threading.Condition()
        self._pending = None
        self._result = None
        self._stop = False
        self._started = False
        self._thread = threading.Thread(
            target=self._run,
            name="hand2-retarget",
            daemon=True,
        )

    def start(self):
        if not self._started:
            self._started = True
            if self.debug_timing:
                reset_optimizer_timing(self.pipeline.retargeter)
            self._thread.start()

    def submit(self, item):
        """Replace the pending work item and return whether one was superseded."""
        with self._condition:
            if self._stop:
                return False
            superseded = self._pending is not None
            self._pending = item
            self._condition.notify()
            return superseded

    def take_result(self):
        """Return the newest completed result without blocking the control loop."""
        with self._condition:
            result, self._result = self._result, None
            return result

    def stop(self, timeout=1.0):
        with self._condition:
            self._stop = True
            self._pending = None
            self._condition.notify_all()
        if self._started and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._stop:
                    self._condition.wait()
                if self._stop:
                    return
                item, self._pending = self._pending, None

            started = time.perf_counter()
            try:
                qpos = self.pipeline.retarget(item["message"]["keypoints"]).reshape(5, 4)
                error = None
            except BaseException as exc:
                qpos = None
                error = exc
            completed = time.perf_counter()

            opt_iters = None
            if self.debug_timing:
                optimizer = getattr(self.pipeline.retargeter, "optimizer", None)
                timing = (
                    optimizer.get_timing_stats()
                    if optimizer is not None and hasattr(optimizer, "get_timing_stats")
                    else None
                )
                if timing is not None and timing.iter_counts:
                    opt_iters = timing.iter_counts[-1]
                reset_optimizer_timing(self.pipeline.retargeter)

            result = dict(item)
            result.update(
                qpos=qpos,
                error=error,
                retarget_ms=(completed - started) * 1000.0,
                retarget_queue_ms=max(
                    0.0, (started - item["frame_start_perf"]) * 1000.0
                ),
                frame_ms=max(
                    0.0, (completed - item["frame_start_perf"]) * 1000.0
                ),
                opt_iters=opt_iters,
            )
            with self._condition:
                # The 200 Hz consumer should normally take every result. If it
                # is briefly delayed, a newer completed target is always safer
                # than replaying an older one.
                self._result = result


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
    parser.add_argument("--retarget-maxeval", type=int, default=None, help="Override the NLopt evaluations per frame. Lower bounds worst-case CPU latency; omit to keep the optimizer default.")
    parser.add_argument("--print-every", type=float, default=1.0, help="Seconds between status prints.")
    parser.add_argument("--socket-timeout", type=float, default=1.0, help="Seconds to wait for a glove frame before printing a timeout warning.")
    parser.add_argument("--command-timeout", type=float, default=1.0, help="Disable Hand 2 if no valid frame arrives for this many seconds. 0 disables the watchdog.")
    parser.add_argument("--debug-latency", action="store_true", help="Print socket/frame/hand-write timing summaries for latency diagnosis.")
    parser.add_argument("--debug-slow-ms", type=float, default=0.0, help="Print slow-frame details above this server processing time in ms. Default derives from --rate.")
    parser.add_argument("--home-on-shutdown", action=argparse.BooleanOptionalAction, default=False, help="Opt-in: move Hand 2 to zero before shutdown.")
    parser.add_argument("--home-duration", type=float, default=1.5, help="Seconds to spend moving to zero position before shutdown.")
    parser.add_argument("--telemetry", action=argparse.BooleanOptionalAction, default=True, help="Publish Hand 2 command/state telemetry to Hammerhead DataCollector.")
    parser.add_argument("--hand-command-telemetry", action=argparse.BooleanOptionalAction, default=True, help="Publish accepted and applied Hand 2 commands.")
    parser.add_argument("--hand-state-telemetry", action=argparse.BooleanOptionalAction, default=True, help="Publish measured Hand 2 joint positions.")
    parser.add_argument("--telemetry-rate", type=float, default=30.0, help="Fixed DataCollector command/state sample rate in Hz, independent of retarget completion.")
    parser.add_argument("--telemetry-host", default=os.environ.get("DATA_COLLECTOR_HOST", "127.0.0.1"), help="DataCollector host for side-specific default endpoints.")
    parser.add_argument("--hand-command-telemetry-endpoint", default=None, help="Explicit command endpoint; overrides --telemetry-host.")
    parser.add_argument("--hand-state-telemetry-endpoint", default=None, help="Explicit state endpoint; overrides --telemetry-host.")
    return parser.parse_args()


def format_peer(peer):
    if isinstance(peer, tuple) and len(peer) >= 2:
        return f"{peer[0]}:{peer[1]}"
    return str(peer)


def create_hand_telemetry_publishers(args):
    # Attribute fallback keeps direct unit calls made with a minimal Namespace
    # free of network side effects. parse_args() enables telemetry for real CLI use.
    if not getattr(args, "telemetry", False):
        return None, None

    command_publisher = None
    state_publisher = None
    if getattr(args, "hand_command_telemetry", True):
        source = default_source("hand_command", args.hand)
        command_publisher = DataCollectorTelemetryPublisher(
            endpoint=getattr(args, "hand_command_telemetry_endpoint", None)
            or default_endpoint("hand_command", args.hand, args.telemetry_host),
            source=source,
            frame_id=source,
        )
    if getattr(args, "hand_state_telemetry", True):
        source = default_source("hand_state", args.hand)
        state_publisher = DataCollectorTelemetryPublisher(
            endpoint=getattr(args, "hand_state_telemetry_endpoint", None)
            or default_endpoint("hand_state", args.hand, args.telemetry_host),
            source=source,
            frame_id=source,
        )
    return command_publisher, state_publisher


def build_hand_command_payload(
    *,
    hand_side,
    message,
    target_qpos,
    applied_qpos,
    peer,
    dropped_socket,
    dropped_socket_total,
    seq_gap,
    enable_hand,
    applied_to_hand,
    apply_timestamp_ns,
    server_command_timestamp_ns,
    retarget_config,
    telemetry_dropped_count,
    telemetry_sequence=None,
    target_updated=True,
    target_age_ms=0.0,
    controller_tick=None,
):
    payload = {
        "schema": "wuji_hand_command.hand2.v2",
        "hand_model": "WujiHand2",
        "hand_side": hand_side,
        "glove_seq": int(message["seq"]),
        "glove_timestamp": float(message["timestamp"]),
        "glove_clock": "rdk_system_time",
        "source_clock": "hand_server_system_time",
        "clock_sync_assumed": False,
        "input_type": message["type"],
        "retarget_location": (
            "hand_server" if message["type"] == "keypoints_frame" else "rdk"
        ),
        "retarget_config": str(retarget_config),
        "finger_names": FINGER_NAMES,
        "joint_names": JOINT_NAMES,
        "joint_order": JOINT_ORDER,
        "tcp_peer": format_peer(peer),
        "dropped_socket": int(dropped_socket),
        "dropped_socket_total": int(dropped_socket_total),
        "seq_gap": int(seq_gap),
        "enable_hand": bool(enable_hand),
        "applied_to_controller": bool(applied_to_hand),
        "applied_to_hand": bool(applied_to_hand),
        "apply_timestamp_ns": apply_timestamp_ns,
        "server_command_timestamp_ns": int(server_command_timestamp_ns),
        "telemetry_dropped_count": int(telemetry_dropped_count),
        "telemetry_sequence": int(
            message["seq"] if telemetry_sequence is None else telemetry_sequence
        ),
        "target_updated": bool(target_updated),
        "target_age_ms": max(0.0, float(target_age_ms)),
        "controller_tick": (
            None if controller_tick is None else int(controller_tick)
        ),
        "applied_qpos_5x4": None,
        "applied_qpos_flat20": None,
    }
    # Preserve the old data_collect field used by the replay client. For Hand 2
    # keypoint mode this is the server-retargeted target in verified device order.
    payload.update(qpos_fields("received_qpos", target_qpos))
    payload.update(qpos_fields("target_qpos", target_qpos))
    if applied_qpos is not None:
        payload.update(qpos_fields("applied_qpos", applied_qpos))
    if message["type"] == "keypoints_frame":
        payload["glove_keypoints_21x3"] = np.asarray(
            message["keypoints"], dtype=np.float64
        ).tolist()
    return payload


def build_hand_state_payload(
    *,
    hand_side,
    target_qpos,
    applied_qpos,
    actual_qpos,
    actual_timestamp_ns,
    feedback_fresh,
    hand_serial,
    kp,
    kd,
    current_limit,
    enable_hand,
    telemetry_dropped_count,
    telemetry_sequence=None,
    glove_seq=None,
    target_updated=True,
    target_age_ms=0.0,
    controller_tick=None,
):
    payload = {
        "schema": "wuji_hand_state.hand2.v2",
        "hand_model": "WujiHand2",
        "hand_side": hand_side,
        "finger_names": FINGER_NAMES,
        "joint_names": JOINT_NAMES,
        "joint_order": JOINT_ORDER,
        "hand_serial": hand_serial,
        "kp": float(kp),
        "kd": float(kd),
        "current_limit": float(current_limit),
        "enable_hand": bool(enable_hand),
        "state_available": actual_qpos is not None,
        "feedback_fresh": bool(feedback_fresh),
        "actual_timestamp_ns": actual_timestamp_ns,
        "source_clock": "hand_server_system_time",
        "read_error": None if actual_qpos is not None else "feedback_not_available",
        "effort_supported": False,
        "actual_qpos_5x4": None,
        "actual_qpos_flat20": None,
        "actual_effort_5x4": None,
        "actual_effort_flat20": None,
        "telemetry_dropped_count": int(telemetry_dropped_count),
        "telemetry_sequence": (
            None if telemetry_sequence is None else int(telemetry_sequence)
        ),
        "glove_seq": None if glove_seq is None else int(glove_seq),
        "target_updated": bool(target_updated),
        "target_age_ms": max(0.0, float(target_age_ms)),
        "controller_tick": (
            None if controller_tick is None else int(controller_tick)
        ),
    }
    payload.update(qpos_fields("target_qpos", target_qpos))
    if applied_qpos is not None:
        payload.update(qpos_fields("applied_qpos", applied_qpos))
    if actual_qpos is not None:
        payload.update(qpos_fields("actual_qpos", actual_qpos))
    return payload


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
    command_telemetry = None
    state_telemetry = None
    retarget_worker = None
    previous_switch_interval = None
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
        command_telemetry, state_telemetry = create_hand_telemetry_publishers(args)
        if command_telemetry is not None:
            print(
                f"Hand command telemetry: {command_telemetry.source} "
                f"-> {command_telemetry.endpoint}"
            )
        if state_telemetry is not None:
            print(
                f"Hand state telemetry: {state_telemetry.source} "
                f"-> {state_telemetry.endpoint}"
            )

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
        retarget_maxeval = getattr(args, "retarget_maxeval", None)
        if retarget_maxeval is not None:
            if retarget_maxeval <= 0:
                raise ValueError("--retarget-maxeval must be positive")
            optimizer = retargeter.optimizer
            if not hasattr(optimizer, "opt") or not hasattr(optimizer.opt, "set_maxeval"):
                raise RuntimeError("Configured retargeter does not expose an NLopt maxeval setting")
            optimizer.opt.set_maxeval(retarget_maxeval)
            print(f"Retarget NLopt maxeval override: {retarget_maxeval}")
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
        telemetry_rate = max(float(getattr(args, "telemetry_rate", 30.0)), 1.0)
        telemetry_interval = 1.0 / telemetry_rate
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        next_tick = time.monotonic()
        next_telemetry_tick = next_tick
        last_tick = next_tick
        last_version = -1
        last_seq = None
        last_valid_frame_time = time.monotonic()
        received = 0
        dropped_socket_total = 0
        last_qpos = None
        latest_feedback = None
        latest_feedback_timestamp_ns = None
        latest_target_message = None
        latest_target_dropped_socket = 0
        latest_target_seq_gap = 0
        latest_target_updated_perf = None
        last_published_target_seq = None
        telemetry_sequence = 0
        controller_tick = 0
        smoother = QposSmoother(
            tau=args.smooth_tau,
            max_velocity=args.max_joint_velocity,
            enabled=not args.disable_output_smoothing,
        )
        if backend is not None:
            measured_qpos = backend.current_positions().reshape(5, 4)
            smoother.initialize(measured_qpos)
            latest_feedback = measured_qpos.copy()
            latest_feedback_timestamp_ns = time.time_ns()
            print(
                "Output initialized from measured Hand 2 joint positions; "
                "the first teleop frame will be rate-limited."
            )
        retarget_worker = LatestRetargetWorker(
            pipeline,
            debug_timing=args.debug_latency,
        )
        retarget_worker.start()
        # The optimizer objective executes Python callbacks.  A shorter GIL
        # handoff interval prevents a long solve from monopolizing the process
        # for the default 5 ms, which is itself one complete 200 Hz control tick.
        previous_switch_interval = sys.getswitchinterval()
        if previous_switch_interval > 0.001:
            sys.setswitchinterval(0.001)
        print(
            "Retarget scheduling: asynchronous latest-frame worker; "
            "fixed-rate hand output is isolated from optimizer latency "
            f"(Python switch interval={sys.getswitchinterval() * 1000.0:.1f}ms)."
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

            seq = last_seq
            target_message = None
            target_dropped_socket = 0
            target_seq_gap = 0
            completed_result = retarget_worker.take_result()
            if message is not None and version != last_version:
                frame_start = time.perf_counter()
                server_queue_ms = 0.0
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
                dropped_socket_total += dropped
                wire_age_ms = (time.time() - message["timestamp"]) * 1000.0
                stats.inc("input_frames")
                stats.add("wire_age_ms", wire_age_ms)
                stats.inc("seq_gap", seq_gap)
                stats.inc("dropped_socket", dropped)
                if read_metrics is not None:
                    stats.add("socket_wait_ms", read_metrics.get("socket_wait_ms"))
                    stats.add("socket_drain_ms", read_metrics.get("socket_drain_ms"))
                    stats.inc("decoded_frames", read_metrics.get("decoded_frames", 0))
                    stats.inc("malformed_messages", read_metrics.get("malformed_messages", 0))

                work_item = {
                    "message": message,
                    "frame_start_perf": frame_start,
                    "server_queue_ms": server_queue_ms,
                    "wire_age_ms": wire_age_ms,
                    "dropped": dropped,
                    "dropped_socket_total": dropped_socket_total,
                    "seq_gap": seq_gap,
                    "read_metrics": read_metrics,
                }
                if message["type"] == "keypoints_frame":
                    if not np.allclose(message["keypoints"], 0):
                        if retarget_worker.submit(work_item):
                            stats.inc("retarget_superseded")
                        stats.inc("retarget_submitted")
                    else:
                        stats.inc("zero_keypoint_frames")
                else:
                    work_item.update(
                        qpos=np.asarray(message["qpos"], dtype=np.float64).reshape(5, 4),
                        error=None,
                        retarget_ms=0.0,
                        retarget_queue_ms=0.0,
                        frame_ms=(time.perf_counter() - frame_start) * 1000.0,
                        opt_iters=None,
                    )
                    completed_result = work_item

            if completed_result is not None:
                if completed_result["error"] is not None:
                    raise RuntimeError(
                        f"{args.hand} Hand 2 retarget worker failed"
                    ) from completed_result["error"]
                last_qpos = completed_result["qpos"]
                smoother.set_target(last_qpos)
                target_message = completed_result["message"]
                target_dropped_socket = completed_result["dropped"]
                target_seq_gap = completed_result["seq_gap"]
                seq = target_message["seq"]
                retarget_ms = completed_result["retarget_ms"]
                frame_ms = completed_result["frame_ms"]
                server_queue_ms = completed_result["server_queue_ms"]
                wire_age_ms = completed_result["wire_age_ms"]
                read_metrics = completed_result["read_metrics"]
                latest_target_message = target_message
                latest_target_dropped_socket = target_dropped_socket
                latest_target_seq_gap = target_seq_gap
                latest_target_updated_perf = time.monotonic()
                stats.add("retarget_ms", retarget_ms)
                stats.add("retarget_queue_ms", completed_result["retarget_queue_ms"])
                stats.add("server_frame_ms", frame_ms)
                stats.add("opt_iters", completed_result.get("opt_iters"))
                debug_payload = target_message.get("debug") or {}
                if args.debug_latency and debug_payload.get("request_ack"):
                    client_probe_perf = debug_payload.get("client_probe_perf")
                    if client_probe_perf is not None:
                        ack_start = time.perf_counter()
                        try:
                            conn.sendall(
                                encode_message(
                                    make_ack_message(
                                        target_message["seq"],
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
                        f"retarget_queue_ms={completed_result['retarget_queue_ms']:.1f} "
                        f"dropped_in_read={target_dropped_socket} "
                        f"seq_gap={target_seq_gap}",
                        flush=True,
                    )

            command_qpos = smoother.step(dt)
            hand_write_ms = 0.0
            hand_apply_timestamp_ns = None
            if (
                backend is not None
                and args.command_timeout > 0
                and time.monotonic() - last_valid_frame_time > args.command_timeout
            ):
                print(
                    f"No valid {args.hand} command frame for "
                    f"{args.command_timeout:g}s; disabling Hand 2 and ending session",
                    flush=True,
                )
                break
            if command_qpos is not None and backend is not None:
                hand_start = time.perf_counter()
                backend.send(command_qpos.reshape(-1))
                hand_apply_timestamp_ns = time.time_ns()
                hand_write_ms = (time.perf_counter() - hand_start) * 1000.0
                if args.debug_latency:
                    stats.add("thumb_j4_target", command_qpos[0, 3])
            controller_tick += 1
            feedback_fresh = False
            telemetry_now = time.monotonic()
            telemetry_due = (
                latest_target_message is not None
                and command_qpos is not None
                and telemetry_now >= next_telemetry_tick
            )
            if telemetry_due:
                next_telemetry_tick += telemetry_interval
                if telemetry_now - next_telemetry_tick > telemetry_interval:
                    next_telemetry_tick = telemetry_now + telemetry_interval
            if telemetry_due and backend is not None:
                feedback = backend.latest_positions()
                if feedback is not None:
                    latest_feedback = feedback.reshape(5, 4)
                    latest_feedback_timestamp_ns = time.time_ns()
                    feedback_fresh = True
                    if args.debug_latency:
                        stats.add("thumb_j4_feedback", latest_feedback[0, 3])

            if telemetry_due:
                target_message = latest_target_message
                target_updated = target_message["seq"] != last_published_target_seq
                target_age_ms = max(
                    0.0,
                    (telemetry_now - latest_target_updated_perf) * 1000.0,
                )
                per_sample_dropped_socket = (
                    latest_target_dropped_socket if target_updated else 0
                )
                per_sample_seq_gap = latest_target_seq_gap if target_updated else 0
                server_command_timestamp_ns = hand_apply_timestamp_ns or time.time_ns()
                apply_timestamp_ns = hand_apply_timestamp_ns
                if command_telemetry is not None:
                    command_telemetry.publish(
                        sequence=telemetry_sequence,
                        source_timestamp_ns=server_command_timestamp_ns,
                        payload=build_hand_command_payload(
                            hand_side=args.hand,
                            message=target_message,
                            target_qpos=last_qpos,
                            applied_qpos=command_qpos,
                            peer=peer,
                            dropped_socket=per_sample_dropped_socket,
                            dropped_socket_total=dropped_socket_total,
                            seq_gap=per_sample_seq_gap,
                            enable_hand=args.enable_hand,
                            applied_to_hand=backend is not None and command_qpos is not None,
                            apply_timestamp_ns=apply_timestamp_ns,
                            server_command_timestamp_ns=server_command_timestamp_ns,
                            retarget_config=pipeline.config_path,
                            telemetry_dropped_count=command_telemetry.dropped_count,
                            telemetry_sequence=telemetry_sequence,
                            target_updated=target_updated,
                            target_age_ms=target_age_ms,
                            controller_tick=controller_tick,
                        ),
                    )
                # State telemetry represents a measured controller sample. Do
                # not fabricate fresh state from a retained position: skipping
                # it makes the DataCollector gap/sequence gate fail closed.
                if state_telemetry is not None and feedback_fresh:
                    state_timestamp_ns = latest_feedback_timestamp_ns
                    hand_serial = (
                        backend.serial_number if backend is not None else args.hand_sn
                    )
                    state_telemetry.publish(
                        sequence=telemetry_sequence,
                        source_timestamp_ns=state_timestamp_ns,
                        payload=build_hand_state_payload(
                            hand_side=args.hand,
                            target_qpos=last_qpos,
                            applied_qpos=command_qpos,
                            actual_qpos=latest_feedback,
                            actual_timestamp_ns=latest_feedback_timestamp_ns,
                            feedback_fresh=feedback_fresh,
                            hand_serial=hand_serial,
                            kp=args.kp,
                            kd=args.kd,
                            current_limit=args.current_limit,
                            enable_hand=args.enable_hand,
                            telemetry_dropped_count=state_telemetry.dropped_count,
                            telemetry_sequence=telemetry_sequence,
                            glove_seq=target_message["seq"],
                            target_updated=target_updated,
                            target_age_ms=target_age_ms,
                            controller_tick=controller_tick,
                        ),
                    )
                last_published_target_seq = target_message["seq"]
                telemetry_sequence += 1
            stats.add("hand_write_ms", hand_write_ms)
            stats.inc("control_ticks")

            now = time.monotonic()
            if now - last_print >= args.print_every and command_qpos is not None:
                elapsed = max(now - report_start, 1e-9)
                recv_fps = stats.counters["input_frames"] / elapsed
                retarget_fps = stats.count("retarget_ms") / elapsed
                control_fps = stats.counters["control_ticks"] / elapsed
                if args.debug_latency:
                    print(
                        f"latency-server recv={received} recv_fps={recv_fps:.1f} "
                        f"retarget_fps={retarget_fps:.1f} "
                        f"control_fps={control_fps:.1f} seq={seq} "
                        f"clock_wire_age_ms avg/p95/max={stats.avg('wire_age_ms'):.1f}/"
                        f"{stats.percentile('wire_age_ms', 95):.1f}/{stats.max('wire_age_ms'):.1f} "
                        f"server_queue_ms avg/p95/max={stats.avg('server_queue_ms'):.1f}/"
                        f"{stats.percentile('server_queue_ms', 95):.1f}/"
                        f"{stats.max('server_queue_ms'):.1f} "
                        f"retarget_ms avg/p95/max={stats.avg('retarget_ms'):.1f}/"
                        f"{stats.percentile('retarget_ms', 95):.1f}/{stats.max('retarget_ms'):.1f} "
                        f"retarget_queue_ms avg/p95/max={stats.avg('retarget_queue_ms'):.1f}/"
                        f"{stats.percentile('retarget_queue_ms', 95):.1f}/"
                        f"{stats.max('retarget_queue_ms'):.1f} "
                        f"socket_wait_ms avg/max={stats.avg('socket_wait_ms'):.1f}/{stats.max('socket_wait_ms'):.1f} "
                        f"drain_ms avg/max={stats.avg('socket_drain_ms'):.1f}/{stats.max('socket_drain_ms'):.1f} "
                        f"hand_write_ms avg/p95/max={stats.avg('hand_write_ms'):.1f}/"
                        f"{stats.percentile('hand_write_ms', 95):.1f}/{stats.max('hand_write_ms'):.1f} "
                        f"ack_send_ms avg/max={stats.avg('ack_send_ms'):.2f}/"
                        f"{stats.max('ack_send_ms'):.2f} "
                        f"dropped_socket={int(stats.counters['dropped_socket'])} "
                        f"seq_gap={int(stats.counters['seq_gap'])} "
                        f"retarget_superseded={int(stats.counters['retarget_superseded'])} "
                        f"opt_iters avg/p90/max={stats.avg('opt_iters'):.1f}/"
                        f"{stats.percentile('opt_iters', 90):.1f}/{stats.max('opt_iters'):.0f} "
                        f"thumb_J4 target/span={stats.last('thumb_j4_target'):.3f}/"
                        f"{stats.span('thumb_j4_target'):.3f} "
                        f"feedback/span={stats.last('thumb_j4_feedback'):.3f}/"
                        f"{stats.span('thumb_j4_feedback'):.3f} "
                        f"thumb={np.round(command_qpos[0], 3).tolist()} "
                        f"index={np.round(command_qpos[1], 3).tolist()} "
                        "",
                        flush=True,
                    )
                    stats.reset()
                    report_start = now
                else:
                    print(
                        f"recv={received} recv_fps={recv_fps:.1f} "
                        f"retarget_fps={retarget_fps:.1f} "
                        f"control_fps={control_fps:.1f} seq={seq} "
                        f"thumb={np.round(command_qpos[0], 3).tolist()} "
                        f"index={np.round(command_qpos[1], 3).tolist()}",
                        flush=True,
                    )
                    stats.reset()
                    report_start = now
                last_print = now
    finally:
        receiver_stop.set()
        if receiver.is_alive():
            receiver.join(timeout=1.0)
        if retarget_worker is not None:
            retarget_worker.stop(timeout=1.0)
        if previous_switch_interval is not None:
            sys.setswitchinterval(previous_switch_interval)
        if backend is not None:
            if backend.is_enabled and args.home_on_shutdown:
                try:
                    print(f"Homing Hand 2 to zero for {args.home_duration:.1f}s before shutdown.")
                    backend.home(args.home_duration, args.rate)
                except Exception as exc:
                    print(f"Shutdown homing skipped: {exc}")
            backend.close()
        if command_telemetry is not None:
            command_telemetry.close()
        if state_telemetry is not None:
            state_telemetry.close()
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
