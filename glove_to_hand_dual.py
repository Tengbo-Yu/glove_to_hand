import argparse
import signal
import sys
import time
from pathlib import Path

import numpy as np
import wujihandpy


PROJECT_ROOT = Path(__file__).resolve().parent
RETARGETING_ROOT = PROJECT_ROOT / "wuji-retargeting"
RETARGETING_EXAMPLE = RETARGETING_ROOT / "example"

for path in (RETARGETING_ROOT, RETARGETING_EXAMPLE):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from input_devices.wuji_glove_device import WujiGloveDevice
from wuji_retargeting import Retargeter


def resolve_config(config, hand_side):
    if config:
        return Path(config).expanduser().resolve()
    return (
        RETARGETING_EXAMPLE
        / "config"
        / f"adaptive_analytical_wuji_glove_{hand_side}.yaml"
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Control two Wuji Hands from two Wuji Gloves using official wuji-retargeting."
    )
    parser.add_argument("--enable-hand", action="store_true", help="Actually enable and move both Wuji Hands.")
    parser.add_argument("--left-glove-sn", default="", help="Left Wuji Glove serial number.")
    parser.add_argument("--right-glove-sn", default="", help="Right Wuji Glove serial number.")
    parser.add_argument("--left-glove-name", default="glove_l", help="wuji_sdk device name for the left glove.")
    parser.add_argument("--right-glove-name", default="glove_r", help="wuji_sdk device name for the right glove.")
    parser.add_argument("--left-hand-serial", default=None, help="USB serial number for the left Wuji Hand.")
    parser.add_argument("--right-hand-serial", default=None, help="USB serial number for the right Wuji Hand.")
    parser.add_argument("--left-config", default=None, help="Left retargeting YAML config path.")
    parser.add_argument("--right-config", default=None, help="Right retargeting YAML config path.")
    parser.add_argument("--glove-stream", choices=("hand_skeleton", "offline_hand_skeleton", "emf_poses"), default="offline_hand_skeleton", help="Wuji SDK stream for keypoint input.")
    parser.add_argument("--wuji-log-level", default="error", choices=("trace", "debug", "info", "warn", "warning", "error", "off"), help="wuji_sdk internal log level.")
    parser.add_argument("--duration", type=float, default=0.0, help="Run time in seconds. Default 0 runs until Ctrl-C.")
    parser.add_argument("--rate", type=float, default=30.0, help="Command rate in Hz.")
    parser.add_argument("--lowpass", type=float, default=5.0, help="Wuji Hand realtime low-pass cutoff in Hz.")
    parser.add_argument("--print-every", type=float, default=1.0, help="Seconds between status prints.")
    parser.add_argument("--home-on-shutdown", action=argparse.BooleanOptionalAction, default=True, help="Move both Wuji Hands to zero position before shutdown.")
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


def cleanup_input_device(input_device):
    for method_name in ("stop", "cleanup", "close"):
        method = getattr(input_device, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass


class SideState:
    def __init__(self, label, glove_name, glove_sn, hand_serial, config):
        self.label = label
        self.glove_name = glove_name
        self.glove_sn = glove_sn
        self.hand_serial = hand_serial
        self.config_path = resolve_config(config, label)
        self.input_device = None
        self.retargeter = None
        self.hand = None
        self.controller = None
        self.frame_count = 0
        self.last_qpos = None


def setup_side(side, args):
    if not side.config_path.exists():
        raise FileNotFoundError(f"[{side.label}] Retargeting config not found: {side.config_path}")

    print(f"[{side.label}] Config: {side.config_path}")
    print(f"[{side.label}] Glove device name: {side.glove_name}")
    print(f"[{side.label}] Glove stream: {args.glove_stream}")

    side.input_device = WujiGloveDevice(
        hand_side=side.label,
        device_name=side.glove_name,
        sn=side.glove_sn or None,
        stream=args.glove_stream,
        sdk_log_level=args.wuji_log_level,
    )
    side.retargeter = Retargeter.from_yaml(str(side.config_path), side.label)

    if args.enable_hand:
        side.hand, side.controller = create_hand_controller(side.hand_serial, args.lowpass)
        print(f"[{side.label}] Hand enabled and realtime controller started.")
    else:
        print(f"[{side.label}] Dry run only.")


def update_side(side):
    fingers_data = side.input_device.get_fingers_data()
    fingers_pose = fingers_data[f"{side.label}_fingers"]

    if fingers_pose is None or np.allclose(fingers_pose, 0):
        return None

    qpos = side.retargeter.retarget(fingers_pose).reshape(5, 4)

    if side.controller is not None:
        side.controller.set_joint_target_position(qpos)

    side.frame_count += 1
    side.last_qpos = qpos
    return qpos


def shutdown_side(side, args):
    if side.hand is not None:
        if side.controller is not None and args.home_on_shutdown:
            try:
                print(f"[{side.label}] Homing hand to zero for {args.home_duration:.1f}s before shutdown.")
                home_hand(side.controller, args.home_duration, args.rate)
            except Exception as exc:
                print(f"[{side.label}] Shutdown homing skipped: {exc}")
        side.hand.write_joint_enabled(False)
    if side.input_device is not None:
        cleanup_input_device(side.input_device)


def run(args):
    print(f"Mode: {'MOVE HAND' if args.enable_hand else 'DRY RUN'}")

    left = SideState("left", args.left_glove_name, args.left_glove_sn, args.left_hand_serial, args.left_config)
    right = SideState("right", args.right_glove_name, args.right_glove_sn, args.right_hand_serial, args.right_config)
    sides = [left, right]

    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)

    try:
        for side in sides:
            setup_side(side, args)

        if args.enable_hand:
            time.sleep(0.5)
            print("Both hands enabled. Keep Ctrl-C ready and make sure both hands are clear.")
        else:
            print("Dry run: printing retargeted joint targets only. Add --enable-hand to move the hands.")

        interval = 1.0 / args.rate
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        last_print = 0.0

        while not stop_requested and (deadline is None or time.monotonic() < deadline):
            for side in sides:
                update_side(side)

            now = time.monotonic()
            if now - last_print >= args.print_every:
                for side in sides:
                    if side.last_qpos is None:
                        continue
                    print(
                        f"[{side.label}] frames={side.frame_count} "
                        f"thumb={np.round(side.last_qpos[0], 3).tolist()} "
                        f"index={np.round(side.last_qpos[1], 3).tolist()}"
                    )
                last_print = now

            time.sleep(interval)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        for side in sides:
            shutdown_side(side, args)
        print("Stopped. Both hands disabled.")


if __name__ == "__main__":
    run(parse_args())
