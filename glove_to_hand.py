import argparse
import signal
import time

import numpy as np

from hand2_backend import WujiHand2Backend
from retargeting_hand2 import Hand2RetargetPipeline
from wuji_glove_input import WujiGloveDevice


def parse_args():
    parser = argparse.ArgumentParser(
        description="Control Wuji Hand 2 from Wuji Glove using wuji-retargeting."
    )
    parser.add_argument(
        "--enable-hand",
        action="store_true",
        help="Explicitly enable and move Hand 2. Without this flag the run is dry-only.",
    )
    parser.add_argument("--hand", default="right", choices=("left", "right"))
    parser.add_argument("--hand-sn", default="", help="Wuji Hand 2 serial number.")
    parser.add_argument(
        "--hand-address",
        default="",
        help="Discovered Hand 2 address, e.g. 192.168.1.111:7447. Empty auto-discovers.",
    )
    parser.add_argument("--glove-sn", default="", help="Wuji Glove serial number.")
    parser.add_argument("--device-name", default="glove", help="Wuji Glove SDK alias.")
    parser.add_argument("--config", default=None, help="Hand 2 retargeting YAML.")
    parser.add_argument(
        "--glove-stream",
        choices=("hand_skeleton", "offline_hand_skeleton"),
        default="hand_skeleton",
    )
    parser.add_argument(
        "--wuji-log-level",
        default="error",
        choices=("trace", "debug", "info", "warn", "warning", "error", "off"),
    )
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--rate", type=float, default=60.0)
    parser.add_argument("--kp", type=float, default=3.5)
    parser.add_argument("--kd", type=float, default=0.1)
    parser.add_argument("--current-limit", type=float, default=1.5)
    parser.add_argument("--enable-timeout", type=float, default=5.0)
    parser.add_argument("--print-every", type=float, default=1.0)
    parser.add_argument(
        "--home-on-shutdown",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Opt-in: interpolate all joints to zero before disabling.",
    )
    parser.add_argument("--home-duration", type=float, default=1.5)
    return parser.parse_args()


def cleanup_input_device(input_device):
    if input_device is None:
        return
    for method_name in ("stop", "cleanup", "close"):
        method = getattr(input_device, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass
            break


def run(args):
    pipeline = Hand2RetargetPipeline(args.config, args.hand)
    print(f"Config: {pipeline.config_path}")
    print(f"Hand side: {args.hand}")
    print(f"Mode: {'MOVE HAND 2' if args.enable_hand else 'DRY RUN'}")

    input_device = None
    backend = None
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    try:
        input_device = WujiGloveDevice(
            hand_side=args.hand,
            device_name=args.device_name,
            sn=args.glove_sn or None,
            stream=args.glove_stream,
            sdk_log_level=args.wuji_log_level,
        )

        if args.enable_hand:
            backend = WujiHand2Backend(
                args.hand,
                sn=args.hand_sn,
                address=args.hand_address,
                kp=args.kp,
                kd=args.kd,
                current_limit=args.current_limit,
                enable_timeout=args.enable_timeout,
            )
            backend.enable()
            print("Hand 2 enabled. Keep the workspace clear and Ctrl-C ready.")
        else:
            print("Dry run: Hand 2 remains disabled; retargeted targets are printed only.")

        interval = 1.0 / max(args.rate, 1.0)
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        frame_count = 0
        last_print = 0.0
        next_tick = time.monotonic()
        while not stop_requested and (deadline is None or time.monotonic() < deadline):
            fingers_pose = input_device.get_fingers_data()[f"{args.hand}_fingers"]
            if fingers_pose is None or np.allclose(fingers_pose, 0):
                time.sleep(0.01)
                continue
            qpos = pipeline.retarget(fingers_pose)
            if backend is not None:
                backend.send(qpos)

            frame_count += 1
            now = time.monotonic()
            if now - last_print >= args.print_every:
                matrix = qpos.reshape(5, 4)
                print(
                    f"frames={frame_count} thumb={np.round(matrix[0], 3).tolist()} "
                    f"index={np.round(matrix[1], 3).tolist()}"
                )
                last_print = now
            next_tick += interval
            time.sleep(max(0.0, next_tick - time.monotonic()))
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if backend is not None:
            if args.home_on_shutdown and backend.is_enabled:
                try:
                    print(f"Homing Hand 2 for {args.home_duration:.1f}s before shutdown.")
                    backend.home(args.home_duration, args.rate)
                except Exception as exc:
                    print(f"Shutdown homing skipped: {exc}")
            backend.close()
        cleanup_input_device(input_device)
        print("Stopped. Hand 2 disabled." if backend is not None else "Stopped.")


if __name__ == "__main__":
    run(parse_args())
