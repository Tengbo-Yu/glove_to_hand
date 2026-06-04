import argparse
import asyncio
import signal
import time

import numpy as np

from glove_to_hand_common import (
    JOINT_MATRIX_SHAPE,
    SIDE_SWAY_JOINT,
    apply_confidence,
    apply_velocity_limit,
    decode_message,
    format_joint_matrix_help,
    home_hand,
    parse_joint_matrix,
    read_message,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Receive Wuji Glove joint angles over TCP and control Wuji Hand.")
    parser.add_argument("--bind-host", default="0.0.0.0", help="Host/IP to listen on.")
    parser.add_argument("--port", type=int, default=8765, help="TCP port to listen on.")
    parser.add_argument("--keep-listening", action="store_true", help="Keep listening for another glove client after a session ends.")
    parser.add_argument("--enable-hand", action="store_true", help="Actually enable and move Wuji Hand.")
    parser.add_argument("--hand-serial", default=None, help="USB serial number for Wuji Hand when multiple hands are connected.")
    parser.add_argument("--duration", type=float, default=30.0, help="Run time in seconds after glove connects. Use 0 for no time limit.")
    parser.add_argument("--rate", type=float, default=30.0, help="Hand homing/control timing rate in Hz.")
    parser.add_argument("--gain", type=float, default=0.7, help="Global relative angle gain from glove to hand.")
    parser.add_argument("--joint-gains", default=None, help=format_joint_matrix_help("Per-joint gains"))
    parser.add_argument("--max-delta", type=float, default=0.45, help="Global max radians away from hand startup pose.")
    parser.add_argument("--joint-max-deltas", default=None, help=format_joint_matrix_help("Per-joint max deltas"))
    parser.add_argument("--confidence-threshold", type=float, default=0.2, help="Ignore fingers below this IK confidence.")
    parser.add_argument("--calibration-frames", type=int, default=30, help="Received frames used as glove neutral pose.")
    parser.add_argument("--lowpass", type=float, default=15.0, help="Wuji Hand realtime low-pass cutoff in Hz.")
    parser.add_argument("--home-on-start", action=argparse.BooleanOptionalAction, default=True, help="Move Wuji Hand to zero position before following the glove.")
    parser.add_argument("--home-duration", type=float, default=1.5, help="Seconds to spend moving to zero position at startup/shutdown.")
    parser.add_argument("--max-velocity", type=float, default=0.6, help="Per-joint command slew-rate limit in rad/s. Use 0 to disable.")
    parser.add_argument("--invert-side-sway", action=argparse.BooleanOptionalAction, default=True, help="Invert J1 side-sway/abduction for index through pinky fingers.")
    parser.add_argument("--socket-timeout", type=float, default=1.0, help="Seconds to wait for a glove frame before printing a timeout warning.")
    parser.add_argument("--diagnostics", action="store_true", help="Print hand actual position and effort while controlling.")
    return parser.parse_args()


def connect_hand(serial_number):
    import wujihandpy

    if serial_number:
        return wujihandpy.Hand(serial_number=serial_number)
    return wujihandpy.Hand()


async def read_frame(reader, timeout=None):
    while True:
        message = await read_message(reader, timeout=timeout)
        if message["type"] == "hello":
            glove_name = message.get("glove_name", "unknown")
            print(f"Glove client hello: {glove_name}")
            continue
        return message


async def read_latest_frame(reader, timeout=None):
    message = await read_frame(reader, timeout=timeout)
    dropped = 0
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=0.001)
        except asyncio.TimeoutError:
            return message, dropped
        if not line:
            raise EOFError("socket closed")
        try:
            next_message = decode_message(line)
        except ValueError as exc:
            print(f"Ignoring malformed socket message: {exc}")
            continue
        if next_message["type"] == "hello":
            glove_name = next_message.get("glove_name", "unknown")
            print(f"Glove client hello: {glove_name}")
            continue
        message = next_message
        dropped += 1


async def read_glove_neutral(reader, calibration_frames):
    samples = []
    while len(samples) < calibration_frames:
        message = await read_frame(reader, timeout=None)
        samples.append(message["angles"])
    return np.mean(np.stack(samples), axis=0)


async def setup_hand(args, joint_max_deltas):
    if not args.enable_hand:
        print("Dry run: printing target preview only. Add --enable-hand to move the hand.")
        return None, None, None, None, None, None, np.zeros(JOINT_MATRIX_SHAPE, dtype=np.float64)

    import wujihandpy

    hand = connect_hand(args.hand_serial)
    lower = hand.read_joint_lower_limit()
    upper = hand.read_joint_upper_limit()
    hand_neutral = hand.read_joint_actual_position()
    min_target = np.maximum(lower, hand_neutral - joint_max_deltas)
    max_target = np.minimum(upper, hand_neutral + joint_max_deltas)
    previous_target = hand_neutral.copy()

    hand.write_joint_enabled(True)
    controller_cm = hand.realtime_controller(
        enable_upstream=True,
        filter=wujihandpy.filter.LowPass(cutoff_freq=args.lowpass),
    )
    controller = controller_cm.__enter__()
    if args.home_on_start:
        print(f"Homing hand to zero for {args.home_duration:.1f}s.")
        await home_hand(controller, args.home_duration, args.rate)
        hand_neutral = controller.get_joint_actual_position()
        min_target = np.maximum(lower, hand_neutral - joint_max_deltas)
        max_target = np.minimum(upper, hand_neutral + joint_max_deltas)
        previous_target = hand_neutral.copy()
    print("Hand enabled and realtime controller started.")
    return hand, controller_cm, controller, hand_neutral, min_target, max_target, previous_target


def compute_target(args, joint_gains, joint_max_deltas, message, glove_neutral, hand_neutral, min_target, max_target, previous_target, dt):
    glove_angles = message["angles"]
    conf = message["confidence"]
    delta = (glove_angles - glove_neutral) * joint_gains
    if args.invert_side_sway:
        delta[1:, SIDE_SWAY_JOINT] *= -1.0
    delta = np.clip(delta, -joint_max_deltas, joint_max_deltas)

    if args.enable_hand:
        target = np.clip(hand_neutral + delta, min_target, max_target)
    else:
        target = delta

    target = apply_confidence(target, previous_target, conf, args.confidence_threshold)
    target = apply_velocity_limit(target, previous_target, args.max_velocity, dt)
    return glove_angles, conf, delta, target


async def run_connected(reader, writer, args, joint_gains, joint_max_deltas, stop_requested):
    peer = writer.get_extra_info("peername")
    print(f"Glove client connected: {peer}")

    hand = None
    controller_cm = None
    controller = None

    try:
        print(f"Calibrating glove neutral from {args.calibration_frames} frame(s). Keep glove in neutral pose.")
        glove_neutral = await read_glove_neutral(reader, args.calibration_frames)
        print("Glove neutral calibrated. Keep Ctrl-C ready; use --enable-hand only when the hand is clear.")

        hand, controller_cm, controller, hand_neutral, min_target, max_target, previous_target = await setup_hand(
            args,
            joint_max_deltas,
        )

        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        last_print = 0.0
        last_command_time = time.monotonic()
        received_frames = 0
        dropped_socket_frames = 0
        timeouts = 0

        while not stop_requested() and (deadline is None or time.monotonic() < deadline):
            try:
                message, dropped = await read_latest_frame(reader, timeout=args.socket_timeout)
            except asyncio.TimeoutError:
                timeouts += 1
                if timeouts == 1 or timeouts % 5 == 0:
                    print(f"Waiting for glove frames... socket_timeouts={timeouts}")
                continue
            except EOFError:
                print("Glove client disconnected.")
                break

            now = time.monotonic()
            dt = now - last_command_time
            last_command_time = now
            glove_angles, conf, delta, target = compute_target(
                args,
                joint_gains,
                joint_max_deltas,
                message,
                glove_neutral,
                hand_neutral,
                min_target,
                max_target,
                previous_target,
                dt,
            )
            previous_target = target
            received_frames += 1
            dropped_socket_frames += dropped
            timeouts = 0

            if controller is not None:
                controller.set_joint_target_position(target)

            if now - last_print >= 1.0:
                msg = (
                    f"recv={received_frames} seq={message['seq']} "
                    f"conf={np.round(conf, 2).tolist()} "
                    f"delta_f2={np.round(delta[1], 3).tolist()} "
                    f"target_f2={np.round(target[1], 3).tolist()} "
                    f"dropped_socket={dropped_socket_frames}"
                )
                if args.diagnostics:
                    msg += (
                        f" glove_f2={np.round(glove_angles[1], 3).tolist()}"
                        f" neutral_f2={np.round(glove_neutral[1], 3).tolist()}"
                    )
                if args.diagnostics and controller is not None:
                    actual = controller.get_joint_actual_position()
                    msg += f" actual_f2={np.round(actual[1], 3).tolist()}"
                    try:
                        effort = controller.get_joint_actual_effort()
                        msg += f" effort_f2={np.round(effort[1], 3).tolist()}"
                    except RuntimeError as exc:
                        if "Effort feedback requires firmware version" not in str(exc):
                            raise
                        msg += " effort_f2=unsupported"
                print(msg)
                last_print = now
                dropped_socket_frames = 0
    finally:
        if controller_cm is not None:
            if args.enable_hand and args.home_on_start:
                try:
                    print(f"Homing hand to zero for {args.home_duration:.1f}s before shutdown.")
                    await asyncio.shield(home_hand(controller, args.home_duration, args.rate))
                except Exception as exc:
                    print(f"Shutdown homing skipped: {exc}")
            controller_cm.__exit__(None, None, None)
        if hand is not None:
            hand.write_joint_enabled(False)
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass
        print("Stopped. Hand disabled and socket closed.")


async def run(args):
    joint_gains = parse_joint_matrix(args.joint_gains, args.gain)
    joint_max_deltas = parse_joint_matrix(args.joint_max_deltas, args.max_delta)

    stop_requested = False
    active_connection = None
    active_task = None
    loop = asyncio.get_running_loop()

    def request_stop():
        nonlocal stop_requested
        if not stop_requested:
            print("Stop requested; homing before shutdown.")
        stop_requested = True

    def get_stop_requested():
        return stop_requested

    async def handle_client(reader, writer):
        nonlocal active_connection, active_task, stop_requested
        if active_connection is not None:
            peer = writer.get_extra_info("peername")
            print(f"Rejecting extra glove client: {peer}")
            writer.close()
            await writer.wait_closed()
            return
        active_connection = writer
        active_task = asyncio.current_task()
        try:
            await run_connected(reader, writer, args, joint_gains, joint_max_deltas, get_stop_requested)
        finally:
            active_connection = None
            active_task = None
            if not args.keep_listening:
                stop_requested = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    print(f"joint_gains=\n{np.round(joint_gains, 3)}")
    print(f"joint_max_deltas=\n{np.round(joint_max_deltas, 3)}")

    server = await asyncio.start_server(handle_client, args.bind_host, args.port)
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"Hand server listening on {sockets}")

    async with server:
        while not stop_requested:
            await asyncio.sleep(0.1)
        server.close()
        await server.wait_closed()
        if active_connection is not None:
            active_connection.close()
        if active_task is not None:
            await active_task


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
