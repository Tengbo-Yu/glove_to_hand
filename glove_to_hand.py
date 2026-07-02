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
        description="Control Wuji Hand from Wuji Glove using official wuji-retargeting."
    )
    parser.add_argument("--enable-hand", action="store_true", help="Actually enable and move Wuji Hand.")
    parser.add_argument("--hand", default="right", choices=("left", "right"), help="Glove/hand side.")
    parser.add_argument("--hand-serial", default=None, help="USB serial number for Wuji Hand when multiple hands are connected.")
    parser.add_argument("--glove-sn", default="", help="Wuji Glove serial number. Use when multiple Wuji devices are online.")
    parser.add_argument("--device-name", default="glove", help="wuji_sdk device name for Wuji Glove.")
    parser.add_argument("--config", default=None, help="Retargeting YAML config path.")
    parser.add_argument("--glove-stream", choices=("hand_skeleton", "offline_hand_skeleton", "emf_poses"), default="offline_hand_skeleton", help="Wuji SDK stream for keypoint input.")
    parser.add_argument("--wuji-log-level", default="error", choices=("trace", "debug", "info", "warn", "warning", "error", "off"), help="wuji_sdk internal log level.")
    parser.add_argument("--duration", type=float, default=0.0, help="Run time in seconds. Default 0 runs until Ctrl-C.")
    parser.add_argument("--rate", type=float, default=30.0, help="Command rate in Hz.")
    parser.add_argument("--lowpass", type=float, default=5.0, help="Wuji Hand realtime low-pass cutoff in Hz.")
    parser.add_argument("--print-every", type=float, default=1.0, help="Seconds between status prints.")
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


def create_hand_controller(args):
    hand = wujihandpy.Hand(serial_number=args.hand_serial) if args.hand_serial else wujihandpy.Hand()
    hand.write_joint_enabled(True)
    controller = hand.realtime_controller(
        enable_upstream=False,
        filter=wujihandpy.filter.LowPass(cutoff_freq=args.lowpass),
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


def run(args):
    config_path = resolve_config(args.config, args.hand)
    if not config_path.exists():
        raise FileNotFoundError(f"Retargeting config not found: {config_path}")

    print(f"Config: {config_path}")
    print(f"Hand side: {args.hand}")
    print(f"Glove device name: {args.device_name}")
    print(f"Glove stream: {args.glove_stream}")
    print(f"Wuji SDK log level: {args.wuji_log_level}")
    print(f"Mode: {'MOVE HAND' if args.enable_hand else 'DRY RUN'}")

    input_device = WujiGloveDevice(
        hand_side=args.hand,
        device_name=args.device_name,
        sn=args.glove_sn or None,
        stream=args.glove_stream,
        sdk_log_level=args.wuji_log_level,
    )
    retargeter = Retargeter.from_yaml(str(config_path), args.hand)

    hand = None
    controller = None
    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)

    try:
        if args.enable_hand:
            hand, controller = create_hand_controller(args)
            time.sleep(0.5)
            print("Hand enabled. Keep Ctrl-C ready and make sure the hand is clear.")
        else:
            print("Dry run: printing retargeted joint targets only. Add --enable-hand to move the hand.")

        interval = 1.0 / args.rate
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        frame_count = 0
        last_print = 0.0

        while not stop_requested and (deadline is None or time.monotonic() < deadline):
            fingers_data = input_device.get_fingers_data()
            fingers_pose = fingers_data[f"{args.hand}_fingers"]

            if fingers_pose is None or np.allclose(fingers_pose, 0):
                time.sleep(0.01)
                continue

            qpos = retargeter.retarget(fingers_pose).reshape(5, 4)

            if controller is not None:
                controller.set_joint_target_position(qpos)

            frame_count += 1
            now = time.monotonic()
            if now - last_print >= args.print_every:
                print(
                    f"frames={frame_count} "
                    f"thumb={np.round(qpos[0], 3).tolist()} "
                    f"index={np.round(qpos[1], 3).tolist()}"
                )
                last_print = now

            time.sleep(interval)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if hand is not None:
            if controller is not None and args.home_on_shutdown:
                try:
                    print(f"Homing hand to zero for {args.home_duration:.1f}s before shutdown.")
                    home_hand(controller, args.home_duration, args.rate)
                except Exception as exc:
                    print(f"Shutdown homing skipped: {exc}")
            hand.write_joint_enabled(False)
        cleanup_input_device(input_device)
        print("Stopped. Hand disabled." if hand is not None else "Stopped.")


if __name__ == "__main__":
    run(parse_args())
