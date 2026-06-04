import argparse
import asyncio
import signal
import time

import numpy as np
import wujihandpy
from wuji_sdk import SdkManager, WujiException

from glove_to_hand import (
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


class SideState:
    def __init__(self, label, glove_name, hand_serial):
        self.label = label
        self.glove_name = glove_name
        self.hand_serial = hand_serial
        self.glove = None
        self.sub = None
        self.glove_neutral = None
        self.hand = None
        self.controller_cm = None
        self.controller = None
        self.hand_neutral = None
        self.min_target = None
        self.max_target = None
        self.previous_target = None
        self.last_command_time = None
        self.dropped_frames = 0


def parse_args():
    parser = argparse.ArgumentParser(description="Control two Wuji Hands from two Wuji Gloves with shared parameters.")
    parser.add_argument("--enable-hand", action="store_true", help="Actually enable and move Wuji Hands.")
    parser.add_argument("--left-glove-name", default="glove_l", help="Wuji SDK device name for the left glove.")
    parser.add_argument("--right-glove-name", default="glove_r", help="Wuji SDK device name for the right glove.")
    parser.add_argument("--left-hand-serial", default=None, help="USB serial number for the left Wuji Hand.")
    parser.add_argument("--right-hand-serial", default=None, help="USB serial number for the right Wuji Hand.")
    parser.add_argument("--duration", type=float, default=30.0, help="Run time in seconds.")
    parser.add_argument("--rate", type=float, default=30.0, help="Command rate in Hz.")
    parser.add_argument("--gain", type=float, default=0.7, help="Global relative angle gain from glove to hand.")
    parser.add_argument("--joint-gains", default=None, help=format_joint_matrix_help("Per-joint gains"))
    parser.add_argument("--max-delta", type=float, default=0.45, help="Global max radians away from hand startup pose.")
    parser.add_argument("--joint-max-deltas", default=None, help=format_joint_matrix_help("Per-joint max deltas"))
    parser.add_argument("--confidence-threshold", type=float, default=0.2, help="Ignore fingers below this IK confidence.")
    parser.add_argument("--calibration-frames", type=int, default=30, help="Frames used as glove neutral pose.")
    parser.add_argument("--lowpass", type=float, default=15.0, help="Wuji Hand realtime low-pass cutoff in Hz.")
    parser.add_argument("--home-on-start", action=argparse.BooleanOptionalAction, default=True, help="Move Wuji Hands to zero position before following gloves.")
    parser.add_argument("--home-duration", type=float, default=1.5, help="Seconds to spend moving to zero position at startup/shutdown.")
    parser.add_argument("--max-velocity", type=float, default=0.6, help="Per-joint command slew-rate limit in rad/s. Use 0 to disable.")
    parser.add_argument("--invert-side-sway", action=argparse.BooleanOptionalAction, default=True, help="Invert J1 side-sway/abduction for index through pinky fingers.")
    parser.add_argument("--diagnostics", action="store_true", help="Print actual position and effort while controlling.")
    return parser.parse_args()


def connect_glove(manager, device_name):
    try:
        return manager.auto_connect(device_name)
    except WujiException as exc:
        message = str(exc)
        if "Session already exists" in message:
            raise RuntimeError(
                f"Wuji Glove {device_name} is already occupied by another SDK session. "
                "Close Wuji Studio / other Python scripts, or power-cycle/replug the glove, then retry."
            ) from exc
        raise


def connect_hand(serial_number):
    if serial_number:
        return wujihandpy.Hand(serial_number=serial_number)
    return wujihandpy.Hand()


async def setup_side(manager, side, args, joint_max_deltas):
    side.glove = connect_glove(manager, side.glove_name)
    print(f"[{side.label}] Connected glove: {side.glove_name}")

    side.sub = side.glove.hand_joint_angles().subscribe()
    side.glove_neutral = await read_neutral(side.sub, args.calibration_frames)
    print(f"[{side.label}] Glove neutral calibrated.")

    if args.enable_hand:
        side.hand = connect_hand(side.hand_serial)
        lower = side.hand.read_joint_lower_limit()
        upper = side.hand.read_joint_upper_limit()
        side.hand_neutral = side.hand.read_joint_actual_position()
        side.min_target = np.maximum(lower, side.hand_neutral - joint_max_deltas)
        side.max_target = np.minimum(upper, side.hand_neutral + joint_max_deltas)
        side.previous_target = side.hand_neutral.copy()

        side.hand.write_joint_enabled(True)
        side.controller_cm = side.hand.realtime_controller(
            enable_upstream=True,
            filter=wujihandpy.filter.LowPass(cutoff_freq=args.lowpass),
        )
        side.controller = side.controller_cm.__enter__()
        if args.home_on_start:
            print(f"[{side.label}] Homing hand to zero for {args.home_duration:.1f}s.")
            await home_hand(side.controller, args.home_duration, args.rate)
            side.hand_neutral = side.controller.get_joint_actual_position()
            side.min_target = np.maximum(lower, side.hand_neutral - joint_max_deltas)
            side.max_target = np.minimum(upper, side.hand_neutral + joint_max_deltas)
            side.previous_target = side.hand_neutral.copy()
        print(f"[{side.label}] Hand enabled and realtime controller started.")
    else:
        side.hand_neutral = None
        side.min_target = None
        side.max_target = None
        side.previous_target = np.zeros((5, 4), dtype=np.float64)
        print(f"[{side.label}] Dry run only.")

    side.last_command_time = time.monotonic()


async def update_side(side, args, joint_gains, joint_max_deltas):
    frame, dropped = await recv_latest(side.sub, timeout=0.2)
    side.dropped_frames += dropped
    glove_angles = frame_to_array(frame)
    conf = confidence_array(frame)

    delta = (glove_angles - side.glove_neutral) * joint_gains
    if args.invert_side_sway:
        delta[1:, SIDE_SWAY_JOINT] *= -1.0
    delta = np.clip(delta, -joint_max_deltas, joint_max_deltas)

    if args.enable_hand:
        target = np.clip(side.hand_neutral + delta, side.min_target, side.max_target)
    else:
        target = delta

    now = time.monotonic()
    dt = now - side.last_command_time
    side.last_command_time = now
    target = apply_confidence(target, side.previous_target, conf, args.confidence_threshold)
    target = apply_velocity_limit(target, side.previous_target, args.max_velocity, dt)
    side.previous_target = target

    if side.controller is not None:
        side.controller.set_joint_target_position(target)

    return glove_angles, conf, delta, target


async def shutdown_side(side, args):
    if side.sub is not None:
        try:
            side.sub.close()
        except Exception:
            pass
    if side.controller_cm is not None:
        if args.enable_hand and args.home_on_start:
            try:
                print(f"[{side.label}] Homing hand to zero for {args.home_duration:.1f}s before shutdown.")
                await asyncio.shield(home_hand(side.controller, args.home_duration, args.rate))
            except Exception as exc:
                print(f"[{side.label}] Shutdown homing skipped: {exc}")
        side.controller_cm.__exit__(None, None, None)
    if side.hand is not None:
        side.hand.write_joint_enabled(False)


async def run(args):
    joint_gains = parse_joint_matrix(args.joint_gains, args.gain)
    joint_max_deltas = parse_joint_matrix(args.joint_max_deltas, args.max_delta)

    stop_requested = False
    loop = asyncio.get_running_loop()

    def request_stop():
        nonlocal stop_requested
        if not stop_requested:
            print("Stop requested; homing both hands before shutdown.")
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

    left = SideState("left", args.left_glove_name, args.left_hand_serial)
    right = SideState("right", args.right_glove_name, args.right_hand_serial)
    sides = [left, right]

    try:
        try:
            manager.disconnect_all()
        except Exception:
            pass

        await asyncio.gather(*(setup_side(manager, side, args, joint_max_deltas) for side in sides))
        print("Both sides are ready. Keep Ctrl-C ready; use --enable-hand only when both hands are clear.")

        interval = 1.0 / args.rate
        deadline = time.monotonic() + args.duration
        last_print = 0.0
        latest = {}

        while time.monotonic() < deadline and not stop_requested:
            results = await asyncio.gather(
                *(update_side(side, args, joint_gains, joint_max_deltas) for side in sides),
                return_exceptions=True,
            )
            for side, result in zip(sides, results):
                if isinstance(result, asyncio.TimeoutError):
                    continue
                if isinstance(result, Exception):
                    raise result
                latest[side.label] = result

            now = time.monotonic()
            if now - last_print >= 1.0:
                for side in sides:
                    if side.label not in latest:
                        continue
                    glove_angles, conf, delta, target = latest[side.label]
                    msg = (
                        f"[{side.label}] conf={np.round(conf, 2).tolist()} "
                        f"delta_f2={np.round(delta[1], 3).tolist()} "
                        f"target_f2={np.round(target[1], 3).tolist()} "
                        f"dropped={side.dropped_frames}"
                    )
                    if args.diagnostics:
                        msg += (
                            f" glove_f2={np.round(glove_angles[1], 3).tolist()}"
                            f" neutral_f2={np.round(side.glove_neutral[1], 3).tolist()}"
                        )
                    if args.diagnostics and side.controller is not None:
                        actual = side.controller.get_joint_actual_position()
                        msg += f" actual_f2={np.round(actual[1], 3).tolist()}"
                        try:
                            effort = side.controller.get_joint_actual_effort()
                            msg += f" effort_f2={np.round(effort[1], 3).tolist()}"
                        except RuntimeError as exc:
                            if "Effort feedback requires firmware version" not in str(exc):
                                raise
                            msg += " effort_f2=unsupported"
                    print(msg)
                    side.dropped_frames = 0
                last_print = now

            await asyncio.sleep(interval)
    finally:
        await asyncio.gather(*(shutdown_side(side, args) for side in sides), return_exceptions=True)
        manager.disconnect_all()
        print("Stopped. Both hands disabled and gloves disconnected.")


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
