import argparse
import signal
import time

import numpy as np

from hand2_backend import WujiHand2Backend
from retargeting_hand2 import Hand2RetargetPipeline
from wuji_glove_input import WujiGloveDevice


def parse_args():
    parser = argparse.ArgumentParser(
        description="Control two Wuji Hand 2 devices from two Wuji Gloves."
    )
    parser.add_argument("--enable-hand", action="store_true")
    parser.add_argument("--left-glove-sn", default="")
    parser.add_argument("--right-glove-sn", default="")
    parser.add_argument("--left-glove-name", default="glove_l")
    parser.add_argument("--right-glove-name", default="glove_r")
    parser.add_argument("--left-hand-sn", default="")
    parser.add_argument("--right-hand-sn", default="")
    parser.add_argument("--left-hand-address", default="")
    parser.add_argument("--right-hand-address", default="")
    parser.add_argument("--left-config", default=None)
    parser.add_argument("--right-config", default=None)
    parser.add_argument(
        "--glove-stream",
        choices=("hand_skeleton", "offline_hand_skeleton"),
        default="offline_hand_skeleton",
    )
    parser.add_argument(
        "--wuji-log-level",
        default="error",
        choices=("trace", "debug", "info", "warn", "warning", "error", "off"),
    )
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--rate", type=float, default=60.0)
    parser.add_argument("--kp", type=float, default=3.0)
    parser.add_argument("--kd", type=float, default=0.1)
    parser.add_argument("--current-limit", type=float, default=1.5)
    parser.add_argument("--enable-timeout", type=float, default=5.0)
    parser.add_argument("--print-every", type=float, default=1.0)
    parser.add_argument(
        "--home-on-shutdown",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--home-duration", type=float, default=1.5)
    return parser.parse_args()


class SideState:
    def __init__(self, label, args):
        self.label = label
        self.glove_sn = getattr(args, f"{label}_glove_sn")
        self.glove_name = getattr(args, f"{label}_glove_name")
        self.hand_sn = getattr(args, f"{label}_hand_sn")
        self.hand_address = getattr(args, f"{label}_hand_address")
        self.config = getattr(args, f"{label}_config")
        self.pipeline = None
        self.input_device = None
        self.backend = None
        self.frame_count = 0
        self.last_qpos = None

    def setup_retargeting(self):
        self.pipeline = Hand2RetargetPipeline(self.config, self.label)
        print(f"[{self.label}] config: {self.pipeline.config_path}")

    def connect_glove(self, args):
        self.input_device = WujiGloveDevice(
            hand_side=self.label,
            device_name=self.glove_name,
            sn=self.glove_sn or None,
            stream=args.glove_stream,
            sdk_log_level=args.wuji_log_level,
        )

    def connect_hand(self, args):
        self.backend = WujiHand2Backend(
            self.label,
            sn=self.hand_sn,
            address=self.hand_address,
            device_name=f"wuji_hand_2_{self.label}",
            kp=args.kp,
            kd=args.kd,
            current_limit=args.current_limit,
            enable_timeout=args.enable_timeout,
        )
        self.backend.enable()

    def update(self):
        pose = self.input_device.get_fingers_data()[f"{self.label}_fingers"]
        if pose is None or np.allclose(pose, 0):
            return
        qpos = self.pipeline.retarget(pose)
        if self.backend is not None:
            self.backend.send(qpos)
        self.frame_count += 1
        self.last_qpos = qpos

    def close(self, args):
        if self.backend is not None:
            if args.home_on_shutdown and self.backend.is_enabled:
                try:
                    self.backend.home(args.home_duration, args.rate)
                except Exception as exc:
                    print(f"[{self.label}] shutdown homing skipped: {exc}")
            self.backend.close()
        if self.input_device is not None:
            self.input_device.cleanup()


def run(args):
    sides = [SideState("left", args), SideState("right", args)]
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    try:
        # Validate both model mappings before opening or energizing hardware.
        for side in sides:
            side.setup_retargeting()
        for side in sides:
            side.connect_glove(args)
        if args.enable_hand:
            for side in sides:
                side.connect_hand(args)
            print("Both Hand 2 devices enabled. Keep the workspace clear.")
        else:
            print("Dry run: both Hand 2 devices remain disabled.")

        interval = 1.0 / max(args.rate, 1.0)
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        last_print = 0.0
        next_tick = time.monotonic()
        while not stop_requested and (deadline is None or time.monotonic() < deadline):
            for side in sides:
                side.update()
            now = time.monotonic()
            if now - last_print >= args.print_every:
                for side in sides:
                    if side.last_qpos is not None:
                        matrix = side.last_qpos.reshape(5, 4)
                        print(
                            f"[{side.label}] frames={side.frame_count} "
                            f"thumb={np.round(matrix[0], 3).tolist()} "
                            f"index={np.round(matrix[1], 3).tolist()}"
                        )
                last_print = now
            next_tick += interval
            time.sleep(max(0.0, next_tick - time.monotonic()))
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        for side in reversed(sides):
            side.close(args)
        print("Stopped. Both Hand 2 devices disabled.")


if __name__ == "__main__":
    run(parse_args())
