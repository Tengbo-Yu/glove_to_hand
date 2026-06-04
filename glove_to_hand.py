import argparse
import asyncio
import signal
import time

import numpy as np
import wujihandpy
from wuji_sdk import SdkManager, WujiException

from glove_to_hand_common import (
    JOINT_MATRIX_SHAPE,
    SIDE_SWAY_JOINT,
    apply_confidence,
    apply_velocity_limit,
    confidence_array,
    format_joint_matrix_help,
    frame_to_array,
    home_hand,
    parse_joint_matrix,
    read_neutral,
    recv_latest,
)


def connect_glove(manager, device_name):
    try:
        manager.disconnect_all()
    except Exception:
        pass

    try:
        return manager.auto_connect(device_name)
    except WujiException as exc:
        message = str(exc)
        if "Session already exists" in message:
            raise RuntimeError(
                "Wuji Glove is already occupied by another SDK session. "
                "Close Wuji Studio / other Python scripts, or power-cycle/replug the glove, then retry."
            ) from exc
        raise


def parse_args():
    parser = argparse.ArgumentParser(description="Control Wuji Hand from Wuji Glove joint angles.")
    parser.add_argument("--enable-hand", action="store_true", help="Actually enable and move Wuji Hand.")
    parser.add_argument("--hand-serial", default=None, help="USB serial number for Wuji Hand when multiple hands are connected.")
    parser.add_argument("--glove-name", default="glove_0", help="Device name used by wuji-sdk auto_connect.")
    parser.add_argument("--duration", type=float, default=30.0, help="Run time in seconds.")
    parser.add_argument("--rate", type=float, default=30.0, help="Command rate in Hz.")
    parser.add_argument("--gain", type=float, default=0.7, help="Global relative angle gain from glove to hand.")
    parser.add_argument("--joint-gains", default=None, help=format_joint_matrix_help("Per-joint gains"))
    parser.add_argument("--max-delta", type=float, default=0.45, help="Global max radians away from hand startup pose.")
    parser.add_argument("--joint-max-deltas", default=None, help=format_joint_matrix_help("Per-joint max deltas"))
    parser.add_argument("--confidence-threshold", type=float, default=0.2, help="Ignore fingers below this IK confidence.")
    parser.add_argument("--calibration-frames", type=int, default=30, help="Frames used as glove neutral pose.")
    parser.add_argument("--lowpass", type=float, default=15.0, help="Wuji Hand realtime low-pass cutoff in Hz.")
    parser.add_argument("--home-on-start", action=argparse.BooleanOptionalAction, default=True, help="Move Wuji Hand to zero position before following the glove.")
    parser.add_argument("--home-duration", type=float, default=1.5, help="Seconds to spend moving to zero position at startup.")
    parser.add_argument("--max-velocity", type=float, default=0.6, help="Per-joint command slew-rate limit in rad/s. Use 0 to disable.")
    parser.add_argument("--invert-side-sway", action=argparse.BooleanOptionalAction, default=True, help="Invert J1 side-sway/abduction for index through pinky fingers.")
    parser.add_argument("--diagnostics", action="store_true", help="Print hand actual position and effort while controlling.")
    return parser.parse_args()


async def run(args):
    joint_gains = parse_joint_matrix(args.joint_gains, args.gain)
    joint_max_deltas = parse_joint_matrix(args.joint_max_deltas, args.max_delta)

    stop_requested = False
    loop = asyncio.get_running_loop()

    def request_stop():
        nonlocal stop_requested
        if not stop_requested:
            print("Stop requested; homing before shutdown.")
        stop_requested = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    manager = SdkManager.instance()
    print(f"joint_gains=\n{np.round(joint_gains, 3)}")
    print(f"joint_max_deltas=\n{np.round(joint_max_deltas, 3)}")
    devices = manager.scan()
    print(f"Found {len(devices)} glove device(s): {devices}")

    glove = connect_glove(manager, args.glove_name)
    print(f"Connected glove: {args.glove_name}")

    sub = glove.hand_joint_angles().subscribe()
    glove_neutral = await read_neutral(sub, args.calibration_frames)
    print("Glove neutral calibrated. Keep Ctrl-C ready; use --enable-hand only when the hand is clear.")

    hand = None
    controller_cm = None
    controller = None
    previous_target = None

    try:
        if args.enable_hand:
            hand = wujihandpy.Hand(serial_number=args.hand_serial) if args.hand_serial else wujihandpy.Hand()
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
                min_target = np.maximum(lower, hand_neutral - args.max_delta)
                max_target = np.minimum(upper, hand_neutral + args.max_delta)
                previous_target = hand_neutral.copy()
            print("Hand enabled and realtime controller started.")
        else:
            lower = upper = hand_neutral = min_target = max_target = None
            previous_target = np.zeros((5, 4), dtype=np.float64)
            print("Dry run: printing target preview only. Add --enable-hand to move the hand.")

        interval = 1.0 / args.rate
        deadline = time.monotonic() + args.duration
        last_print = 0.0
        last_command_time = time.monotonic()
        dropped_frames = 0

        while time.monotonic() < deadline and not stop_requested:
            try:
                frame, dropped = await recv_latest(sub, timeout=0.2)
            except asyncio.TimeoutError:
                continue
            dropped_frames += dropped
            glove_angles = frame_to_array(frame)
            conf = confidence_array(frame)
            delta = (glove_angles - glove_neutral) * joint_gains
            if args.invert_side_sway:
                delta[1:, SIDE_SWAY_JOINT] *= -1.0
            delta = np.clip(delta, -joint_max_deltas, joint_max_deltas)

            if args.enable_hand:
                target = np.clip(hand_neutral + delta, min_target, max_target)
            else:
                target = delta

            now = time.monotonic()
            dt = now - last_command_time
            last_command_time = now
            target = apply_confidence(target, previous_target, conf, args.confidence_threshold)
            target = apply_velocity_limit(target, previous_target, args.max_velocity, dt)
            previous_target = target

            if controller is not None:
                controller.set_joint_target_position(target)

            if now - last_print >= 1.0:
                msg = (
                    f"conf={np.round(conf, 2).tolist()} "
                    f"delta_f2={np.round(delta[1], 3).tolist()} "
                    f"target_f2={np.round(target[1], 3).tolist()} "
                    f"dropped={dropped_frames}"
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
                dropped_frames = 0

            await asyncio.sleep(interval)
    finally:
        try:
            sub.close()
        except Exception:
            pass
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
        manager.disconnect_all()
        print("Stopped. Hand disabled and glove disconnected.")


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
