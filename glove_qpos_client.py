import argparse
import signal
import socket
import sys
import time
from pathlib import Path

import numpy as np

from qpos_protocol import encode_message, make_hello_message, make_qpos_message


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
        description="Read a Wuji Glove, retarget to hand joints, and stream qpos to a hand server over TCP."
    )
    parser.add_argument("--host", default="192.168.123.164", help="hand_qpos_server host/IP.")
    parser.add_argument("--port", type=int, default=8765, help="hand_qpos_server TCP port.")
    parser.add_argument("--hand", default="right", choices=("left", "right"), help="Glove/hand side.")
    parser.add_argument("--glove-sn", default="", help="Wuji Glove serial number. Use when multiple Wuji devices are online.")
    parser.add_argument("--device-name", default="glove", help="wuji_sdk device name for Wuji Glove.")
    parser.add_argument("--config", default=None, help="Retargeting YAML config path.")
    parser.add_argument("--duration", type=float, default=0.0, help="Run time in seconds. Default 0 runs until Ctrl-C.")
    parser.add_argument("--rate", type=float, default=30.0, help="Frame send rate in Hz.")
    parser.add_argument("--connect-timeout", type=float, default=10.0, help="Seconds to wait when connecting to the hand server.")
    parser.add_argument("--print-every", type=float, default=1.0, help="Seconds between status prints.")
    return parser.parse_args()


def cleanup_input_device(input_device):
    for method_name in ("stop", "cleanup", "close"):
        method = getattr(input_device, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass


def open_socket(host, port, connect_timeout):
    sock = socket.create_connection((host, port), timeout=connect_timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(None)
    return sock


def run(args):
    config_path = resolve_config(args.config, args.hand)
    if not config_path.exists():
        raise FileNotFoundError(f"Retargeting config not found: {config_path}")

    print(f"Config: {config_path}")
    print(f"Hand side: {args.hand}")
    print(f"Glove device name: {args.device_name}")
    print(f"Hand server: {args.host}:{args.port}")

    input_device = WujiGloveDevice(
        hand_side=args.hand,
        device_name=args.device_name,
        sn=args.glove_sn or None,
    )
    retargeter = Retargeter.from_yaml(str(config_path), args.hand)

    sock = None
    stop_requested = False
    print("retargeter")
    # breakpoint()
    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)

    try:
        sock = open_socket(args.host, args.port, args.connect_timeout)
        print('----- hello ')
        sock.sendall(encode_message(make_hello_message(args.hand, time.time())))
        print(f"Streaming retargeted qpos to {args.host}:{args.port}")

        interval = 1.0 / args.rate
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        seq = 0
        last_print = 0.0

        while not stop_requested and (deadline is None or time.monotonic() < deadline):
            fingers_data = input_device.get_fingers_data()
            fingers_pose = fingers_data[f"{args.hand}_fingers"]

            if fingers_pose is None or np.allclose(fingers_pose, 0):
                time.sleep(0.01)
                continue

            qpos = retargeter.retarget(fingers_pose).reshape(5, 4)
            print(qpos)
            sock.sendall(encode_message(make_qpos_message(seq, qpos, time.time())))
            seq += 1

            now = time.monotonic()
            if now - last_print >= args.print_every:
                print(
                    f"sent={seq} "
                    f"thumb={np.round(qpos[0], 3).tolist()} "
                    f"index={np.round(qpos[1], 3).tolist()}"
                )
                last_print = now

            time.sleep(interval)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        cleanup_input_device(input_device)
        print("Stopped. Glove disconnected and socket closed.")


if __name__ == "__main__":
    run(parse_args())
