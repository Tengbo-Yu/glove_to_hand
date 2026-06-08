import argparse
import os
import signal
import socket
import time

import numpy as np
import wujihandpy

from data_collector_telemetry import (
    DataCollectorTelemetryPublisher,
    JOINT_NAMES,
    FINGER_NAMES,
    default_endpoint,
    default_source,
    qpos_fields,
    seconds_to_ns,
)
from qpos_protocol import SocketLineReader, decode_message


def parse_args():
    parser = argparse.ArgumentParser(
        description="Receive retargeted qpos over TCP and drive a Wuji Hand."
    )
    parser.add_argument("--bind-host", default="0.0.0.0", help="Host/IP to listen on.")
    parser.add_argument("--port", type=int, default=8765, help="TCP port to listen on.")
    parser.add_argument("--keep-listening", action="store_true", help="Keep listening for another glove client after a session ends.")
    parser.add_argument("--hand", default="right", choices=("left", "right"), help="Wuji hand side served by this process.")
    parser.add_argument("--enable-hand", action="store_true", help="Actually enable and move Wuji Hand.")
    parser.add_argument("--hand-serial", default=None, help="USB serial number for Wuji Hand when multiple hands are connected.")
    parser.add_argument("--duration", type=float, default=0.0, help="Run time in seconds after glove connects. Default 0 runs until Ctrl-C.")
    parser.add_argument("--rate", type=float, default=30.0, help="Hand homing timing rate in Hz.")
    parser.add_argument("--lowpass", type=float, default=5.0, help="Wuji Hand realtime low-pass cutoff in Hz.")
    parser.add_argument("--print-every", type=float, default=1.0, help="Seconds between status prints.")
    parser.add_argument("--socket-timeout", type=float, default=1.0, help="Seconds to wait for a glove frame before printing a timeout warning.")
    parser.add_argument("--home-on-shutdown", action=argparse.BooleanOptionalAction, default=True, help="Move Wuji Hand to zero position before shutdown.")
    parser.add_argument("--home-duration", type=float, default=1.5, help="Seconds to spend moving to zero position before shutdown.")
    parser.add_argument("--telemetry", action=argparse.BooleanOptionalAction, default=True, help="Publish hand command/state telemetry to DataCollector.")
    parser.add_argument("--hand-command-telemetry", action=argparse.BooleanOptionalAction, default=True, help="Publish received hand command telemetry.")
    parser.add_argument("--hand-state-telemetry", action=argparse.BooleanOptionalAction, default=True, help="Publish Wuji Hand state telemetry.")
    parser.add_argument("--telemetry-host", default=os.environ.get("DATA_COLLECTOR_HOST", "127.0.0.1"), help="DataCollector host used for default Wuji hand telemetry endpoints.")
    parser.add_argument("--hand-command-telemetry-endpoint", default=None, help="Explicit DataCollector endpoint for hand command telemetry. Default is selected from --hand.")
    parser.add_argument("--hand-state-telemetry-endpoint", default=None, help="Explicit DataCollector endpoint for hand state telemetry. Default is selected from --hand.")
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


def create_hand_controller(hand_serial, lowpass, enable_upstream=False):
    hand = wujihandpy.Hand(serial_number=hand_serial) if hand_serial else wujihandpy.Hand()
    hand.write_joint_enabled(True)
    controller = hand.realtime_controller(
        enable_upstream=enable_upstream,
        filter=wujihandpy.filter.LowPass(cutoff_freq=lowpass),
    )
    return hand, controller


def format_peer(peer):
    if isinstance(peer, tuple) and len(peer) >= 2:
        return f"{peer[0]}:{peer[1]}"
    return str(peer)


def create_hand_telemetry_publishers(args):
    if not args.telemetry:
        return None, None

    command_publisher = None
    state_publisher = None
    if args.hand_command_telemetry:
        command_source = default_source("hand_command", args.hand)
        command_publisher = DataCollectorTelemetryPublisher(
            endpoint=args.hand_command_telemetry_endpoint
            or default_endpoint("hand_command", args.hand, args.telemetry_host),
            source=command_source,
            frame_id=command_source,
        )
    if args.hand_state_telemetry:
        state_source = default_source("hand_state", args.hand)
        state_publisher = DataCollectorTelemetryPublisher(
            endpoint=args.hand_state_telemetry_endpoint
            or default_endpoint("hand_state", args.hand, args.telemetry_host),
            source=state_source,
            frame_id=state_source,
        )
    return command_publisher, state_publisher


def publish_hand_command(
    *,
    publisher,
    hand_side,
    message,
    peer,
    dropped_socket,
    dropped_socket_total,
    enable_hand,
    applied_to_controller,
    apply_timestamp_ns,
):
    if publisher is None:
        return False
    qpos = message["qpos"]
    payload = {
        "hand_side": hand_side,
        "glove_seq": int(message["seq"]),
        "glove_timestamp": float(message["timestamp"]),
        "finger_names": FINGER_NAMES,
        "joint_names": JOINT_NAMES,
        "tcp_peer": format_peer(peer),
        "dropped_socket": int(dropped_socket),
        "dropped_socket_total": int(dropped_socket_total),
        "enable_hand": bool(enable_hand),
        "applied_to_controller": bool(applied_to_controller),
        "apply_timestamp_ns": apply_timestamp_ns,
        "telemetry_dropped_count": int(publisher.dropped_count),
    }
    payload.update(qpos_fields("received_qpos", qpos))
    return publisher.publish(
        sequence=int(message["seq"]),
        payload=payload,
        source_timestamp_ns=seconds_to_ns(float(message["timestamp"])),
    )


def build_hand_state_payload(
    *,
    hand_side,
    controller,
    target_qpos,
    hand_serial,
    lowpass,
    enable_upstream,
):
    payload = {
        "hand_side": hand_side,
        "finger_names": FINGER_NAMES,
        "joint_names": JOINT_NAMES,
        "hand_serial": hand_serial,
        "lowpass": float(lowpass),
        "enable_upstream": bool(enable_upstream),
        "state_available": False,
        "read_error": None,
        "effort_supported": False,
        "actual_qpos_5x4": None,
        "actual_qpos_flat20": None,
        "actual_effort_5x4": None,
        "actual_effort_flat20": None,
    }
    payload.update(qpos_fields("target_qpos", target_qpos))

    if controller is None:
        payload["read_error"] = "controller_not_available"
        return payload

    try:
        actual_qpos = controller.get_joint_actual_position()
    except Exception as exc:
        payload["read_error"] = str(exc)
        return payload

    payload["state_available"] = True
    payload.update(qpos_fields("actual_qpos", actual_qpos))

    try:
        actual_effort = controller.get_joint_actual_effort()
    except Exception as exc:
        payload["read_error"] = str(exc)
        return payload

    payload["effort_supported"] = True
    payload.update(qpos_fields("actual_effort", actual_effort))
    return payload


def publish_hand_state(
    *,
    publisher,
    sequence,
    source_timestamp_ns,
    hand_side,
    controller,
    target_qpos,
    hand_serial,
    lowpass,
    enable_upstream,
):
    if publisher is None:
        return False
    payload = build_hand_state_payload(
        hand_side=hand_side,
        controller=controller,
        target_qpos=target_qpos,
        hand_serial=hand_serial,
        lowpass=lowpass,
        enable_upstream=enable_upstream,
    )
    payload["telemetry_dropped_count"] = int(publisher.dropped_count)
    return publisher.publish(
        sequence=int(sequence),
        payload=payload,
        source_timestamp_ns=source_timestamp_ns,
    )


def read_latest_qpos(reader):
    """Drain all buffered lines, returning the newest qpos message.

    Returns (message_or_None, dropped). None means no full frame was available
    before the socket timeout (so the caller should keep waiting).
    """
    message = None
    dropped = 0

    def process_line(line):
        nonlocal message, dropped
        if line is None:
            return
        try:
            decoded = decode_message(line)
        except ValueError as exc:
            print(f"Ignoring malformed socket message: {exc}")
            return
        if decoded["type"] == "hello":
            print(f"Glove client hello: hand_side={decoded.get('hand_side', 'unknown')}")
            return
        if message is not None:
            dropped += 1
        message = decoded

    process_line(reader.read_line())
    while True:
        line = reader.read_available_line()
        if line is None:
            break
        process_line(line)
    return message, dropped


def serve_connection(conn, peer, args, stop_requested):
    print(f"Glove client connected: {peer}")
    conn.settimeout(args.socket_timeout)
    reader = SocketLineReader(conn)

    hand = None
    controller = None
    command_telemetry = None
    state_telemetry = None
    enable_upstream = False

    try:
        command_telemetry, state_telemetry = create_hand_telemetry_publishers(args)
        enable_upstream = state_telemetry is not None
        if command_telemetry is not None:
            print(f"Hand command telemetry: {command_telemetry.source} -> {command_telemetry.endpoint}")
        if state_telemetry is not None:
            print(f"Hand state telemetry: {state_telemetry.source} -> {state_telemetry.endpoint}")

        if args.enable_hand:
            hand, controller = create_hand_controller(args.hand_serial, args.lowpass, enable_upstream=enable_upstream)
            time.sleep(0.5)
            print("Hand enabled and realtime controller started.")
        else:
            print("Dry run: printing received qpos only. Add --enable-hand to move the hand.")

        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        received = 0
        dropped_socket_window = 0
        dropped_socket_total = 0
        timeouts = 0
        last_print = 0.0
        last_qpos = None

        while not stop_requested() and (deadline is None or time.monotonic() < deadline):
            try:
                message, dropped = read_latest_qpos(reader)
            except EOFError:
                print("Glove client disconnected.")
                break

            if message is None:
                timeouts += 1
                if timeouts == 1 or timeouts % 5 == 0:
                    print(f"Waiting for glove frames... socket_timeouts={timeouts}")
                continue

            timeouts = 0
            received += 1
            dropped_socket_window += dropped
            dropped_socket_total += dropped
            last_qpos = message["qpos"]
            applied_to_controller = False
            apply_timestamp_ns = None

            if controller is not None:
                controller.set_joint_target_position(last_qpos)
                applied_to_controller = True
                apply_timestamp_ns = time.time_ns()

            publish_hand_command(
                publisher=command_telemetry,
                hand_side=args.hand,
                message=message,
                peer=peer,
                dropped_socket=dropped,
                dropped_socket_total=dropped_socket_total,
                enable_hand=args.enable_hand,
                applied_to_controller=applied_to_controller,
                apply_timestamp_ns=apply_timestamp_ns,
            )
            publish_hand_state(
                publisher=state_telemetry,
                sequence=message["seq"],
                source_timestamp_ns=apply_timestamp_ns or seconds_to_ns(message["timestamp"]),
                hand_side=args.hand,
                controller=controller,
                target_qpos=last_qpos,
                hand_serial=args.hand_serial,
                lowpass=args.lowpass,
                enable_upstream=enable_upstream,
            )

            now = time.monotonic()
            if now - last_print >= args.print_every:
                print(
                    f"recv={received} seq={message['seq']} "
                    f"thumb={np.round(last_qpos[0], 3).tolist()} "
                    f"index={np.round(last_qpos[1], 3).tolist()} "
                    f"dropped_socket={dropped_socket_window}"
                )
                last_print = now
                dropped_socket_window = 0
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
        if command_telemetry is not None:
            command_telemetry.close()
        if state_telemetry is not None:
            state_telemetry.close()
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
    print(f"Hand side: {args.hand}")
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
