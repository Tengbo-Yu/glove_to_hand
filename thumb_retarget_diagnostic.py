"""Observe Wuji Glove thumb input and Hand 2 targets without enabling hardware."""

from __future__ import annotations

import argparse
import signal
import time

import numpy as np

from retargeting_hand2 import Hand2RetargetPipeline
from wuji_glove_input import WujiGloveDevice


def bend_angle_deg(a, b, c) -> float:
    """Return flexion magnitude at b; a-b-c straight is zero degrees."""
    incoming = np.asarray(b, dtype=np.float64) - np.asarray(a, dtype=np.float64)
    outgoing = np.asarray(c, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    denom = np.linalg.norm(incoming) * np.linalg.norm(outgoing)
    if denom < 1e-10:
        return float("nan")
    cosine = np.clip(np.dot(incoming, outgoing) / denom, -1.0, 1.0)
    return float(np.degrees(np.arccos(cosine)))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hand", choices=("left", "right"), default="right")
    parser.add_argument("--glove-sn", default="WG1KA03260512012")
    parser.add_argument("--device-name", default="glove_r_thumb_diag")
    parser.add_argument(
        "--stream",
        choices=("hand_skeleton", "offline_hand_skeleton"),
        default="hand_skeleton",
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--rate", type=float, default=120.0)
    parser.add_argument("--print-every", type=float, default=0.25)
    return parser.parse_args()


def finite_span(values) -> float:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    return float(np.ptp(array)) if array.size else 0.0


def run(args):
    if args.duration <= 0 or args.rate <= 0 or args.print_every <= 0:
        raise ValueError("--duration, --rate and --print-every must be positive")

    pipeline = Hand2RetargetPipeline(args.config, args.hand)
    input_device = WujiGloveDevice(
        hand_side=args.hand,
        device_name=args.device_name,
        sn=args.glove_sn or None,
        stream=args.stream,
        sdk_log_level="error",
        emf_rate_divider=None,
    )

    stopped = False

    def request_stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)

    input_ip_samples = []
    target_ip_raw_samples = []
    target_ip_filtered_samples = []
    fresh_frames = 0
    start = time.monotonic()
    warmup_until = start + min(1.0, args.duration * 0.2)
    deadline = start + args.duration
    next_tick = start
    last_print = 0.0

    print(f"Thumb diagnostic: stream={args.stream} config={pipeline.config_path}")
    print("Bend only the distal thumb joint repeatedly; Hand 2 remains disconnected.")

    try:
        while not stopped and time.monotonic() < deadline:
            fingers = input_device.get_fingers_data()[f"{args.hand}_fingers"]
            debug = input_device.get_debug_stats()
            if fingers is not None and not np.allclose(fingers, 0):
                filtered_urdf, verbose = pipeline.retargeter.retarget_verbose(fingers)
                raw_device = np.asarray(verbose["qpos_unfiltered"])[
                    pipeline.qpos_permutation
                ]
                filtered_device = np.asarray(filtered_urdf)[pipeline.qpos_permutation]
                thumb_mcp_deg = bend_angle_deg(fingers[1], fingers[2], fingers[3])
                thumb_ip_deg = bend_angle_deg(fingers[2], fingers[3], fingers[4])

                if debug.get("last_poll_new_frames", 0) > 0:
                    fresh_frames += 1
                now = time.monotonic()
                if now >= warmup_until:
                    input_ip_samples.append(thumb_ip_deg)
                    target_ip_raw_samples.append(float(raw_device[3]))
                    target_ip_filtered_samples.append(float(filtered_device[3]))

                if now - last_print >= args.print_every:
                    print(
                        f"fresh={fresh_frames:4d} input_bend_deg "
                        f"mcp={thumb_mcp_deg:6.1f} ip={thumb_ip_deg:6.1f} | "
                        f"target_thumb_rad={np.round(filtered_device[:4], 3).tolist()} "
                        f"J4_raw={raw_device[3]:+.3f} J4_out={filtered_device[3]:+.3f}",
                        flush=True,
                    )
                    last_print = now

            next_tick += 1.0 / args.rate
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif -delay > 1.0 / args.rate:
                next_tick = time.monotonic()
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        input_device.cleanup()

    input_span = finite_span(input_ip_samples)
    raw_span = finite_span(target_ip_raw_samples)
    output_span = finite_span(target_ip_filtered_samples)
    print(
        f"SUMMARY fresh_frames={fresh_frames} input_IP_span={input_span:.1f}deg "
        f"target_J4_raw_span={raw_span:.3f}rad target_J4_out_span={output_span:.3f}rad"
    )
    if input_span < 5.0:
        print(
            "RESULT: glove skeleton thumb-IP input is nearly static. Compare with "
            "--stream offline_hand_skeleton and check the active glove calibration user."
        )
    elif raw_span < 0.05:
        print(
            "RESULT: glove input moves but the retarget optimizer freezes thumb J4. "
            "The Hand 2 profile needs a thumb-specific correction."
        )
    elif output_span < 0.05:
        print("RESULT: raw target moves but filtering suppresses thumb J4.")
    else:
        print(
            "RESULT: retargeting sends a moving thumb J4 target. Test physical thumb "
            "J4 as zero-based device index 3 and inspect its diagnostics."
        )


if __name__ == "__main__":
    run(parse_args())
