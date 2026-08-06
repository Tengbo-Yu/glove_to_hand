"""Read-only Wuji Hand 2 health check with an opt-in small-motion test."""

import argparse
import time

import numpy as np

from hand2_backend import WujiHand2Backend


HAND2_POSITION_LIMITS = np.array(
    [
        [-1.187, 1.291],
        [-1.484, 0.698],
        [-1.047, 1.570],
        [-1.047, 1.570],
        *([[-1.047, 1.570], [-0.698, 0.698], [-1.047, 2.094], [-1.047, 1.570]] * 4),
    ],
    dtype=np.float64,
)


def send_for(hand, positions, duration, rate):
    """Continuously publish one target so the device command stays fresh."""
    interval = 1.0 / rate
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        hand.send(positions)
        time.sleep(interval)


def ramp(hand, start, target, duration, rate):
    """Continuously publish a linear position ramp."""
    steps = max(1, int(np.ceil(duration * rate)))
    interval = 1.0 / rate
    for step in range(1, steps + 1):
        alpha = step / steps
        hand.send((1.0 - alpha) * start + alpha * target)
        time.sleep(interval)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hand", choices=("left", "right"), default="right")
    parser.add_argument("--hand-sn", default="")
    parser.add_argument("--hand-address", default="")
    parser.add_argument(
        "--enable-motion",
        action="store_true",
        help="Explicitly enable Hand 2 and perform the small motion below.",
    )
    parser.add_argument(
        "--joint",
        type=int,
        default=3,
        help="Zero-based device joint index 0..19; thumb J4 is index 3.",
    )
    parser.add_argument("--delta", type=float, default=0.25, help="Test offset in radians.")
    parser.add_argument("--hold", type=float, default=3.0, help="Seconds to hold the target.")
    parser.add_argument("--ramp", type=float, default=0.75, help="Seconds per position ramp.")
    parser.add_argument("--rate", type=float, default=50.0, help="Command publishing rate in Hz.")
    parser.add_argument("--kp", type=float, default=1.0)
    parser.add_argument("--kd", type=float, default=0.05)
    parser.add_argument("--current-limit", type=float, default=0.5)
    return parser.parse_args()


def main():
    args = parse_args()
    if not 0 <= args.joint < 20:
        raise ValueError("--joint must be in 0..19")
    if not np.isfinite(args.delta):
        raise ValueError("--delta must be finite")
    if args.hold <= 0 or args.ramp <= 0 or args.rate <= 0:
        raise ValueError("--hold, --ramp, and --rate must be positive")
    hand = WujiHand2Backend(
        args.hand,
        sn=args.hand_sn,
        address=args.hand_address,
        device_name=f"wuji_hand_2_{args.hand}_test",
        kp=args.kp,
        kd=args.kd,
        current_limit=args.current_limit,
    )
    try:
        positions = hand.current_positions()
        frame = hand.diagnostics()
        if frame is None:
            raise TimeoutError("No Hand 2 diagnostic frame received")
        errors = [
            (int(entry.nid), int(entry.error_code_current))
            for entry in frame.joints
            if int(entry.error_code_current) != 0
        ]
        voltages = [float(entry.vbus_v_fb) for entry in frame.joints]
        temperatures = [float(entry.mcu_temp_c_fb) for entry in frame.joints]
        print(f"positions(rad): {np.round(positions, 4).tolist()}")
        print(f"diagnostic joints: {len(frame.joints)}/20; errors: {errors}")
        print(f"vbus range: {min(voltages):.2f}..{max(voltages):.2f} V")
        print(f"MCU temperature range: {min(temperatures):.1f}..{max(temperatures):.1f} C")

        if not args.enable_motion:
            print("Read-only check complete; Hand 2 was not enabled.")
            return

        target = positions.copy()
        target[args.joint] += args.delta
        lower, upper = HAND2_POSITION_LIMITS[args.joint]
        if not lower <= target[args.joint] <= upper:
            raise ValueError(
                f"Joint {args.joint} target {target[args.joint]:.4f} rad exceeds "
                f"the Hand 2 model limit [{lower:.4f}, {upper:.4f}]"
            )

        hand.enable()
        enabled_frame = hand.diagnostics()
        enabled_nids = [] if enabled_frame is None else [
            int(entry.nid)
            for entry in enabled_frame.joints
            if int(entry.status_word.ext_state) == hand._ENABLED_EXT_STATE
        ]
        print(
            f"Enable verified by diagnostics: {len(enabled_nids)}/20 joints; "
            "the Hand 2 logo should be solid white during this test.",
            flush=True,
        )

        send_for(hand, positions, 0.5, args.rate)
        ramp(hand, positions, target, args.ramp, args.rate)
        send_for(hand, target, args.hold, args.rate)
        reached = hand.current_positions()
        observed_delta = reached[args.joint] - positions[args.joint]
        print(
            f"Target reached: start={positions[args.joint]:.4f}, "
            f"target={target[args.joint]:.4f}, measured={reached[args.joint]:.4f}, "
            f"observed_delta={observed_delta:+.4f} rad",
            flush=True,
        )

        ramp(hand, reached, positions, args.ramp, args.rate)
        send_for(hand, positions, 0.5, args.rate)
        returned = hand.current_positions()
        return_error = returned[args.joint] - positions[args.joint]
        print(
            f"Motion test complete: joint={args.joint}, commanded_delta={args.delta:+g} rad, "
            f"return_error={return_error:+.4f} rad"
        )
    finally:
        hand.close()


if __name__ == "__main__":
    main()
