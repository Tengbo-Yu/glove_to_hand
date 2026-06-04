import argparse
import asyncio
import signal
import time

import numpy as np
from wuji_sdk import SdkManager, WujiException

from glove_to_hand_common import (
    confidence_array,
    frame_to_array,
    make_frame_message,
    make_hello_message,
    recv_latest,
    write_message,
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
    parser = argparse.ArgumentParser(description="Stream Wuji Glove joint angles to a hand_server over TCP.")
    parser.add_argument("--host", default="127.0.0.1", help="hand_server host/IP.")
    parser.add_argument("--port", type=int, default=8765, help="hand_server TCP port.")
    parser.add_argument("--glove-name", default="glove_0", help="Device name used by wuji-sdk auto_connect.")
    parser.add_argument("--duration", type=float, default=30.0, help="Run time in seconds. Use 0 for no time limit.")
    parser.add_argument("--rate", type=float, default=30.0, help="Frame send rate in Hz.")
    parser.add_argument("--connect-timeout", type=float, default=10.0, help="Seconds to wait when connecting to hand_server.")
    parser.add_argument("--diagnostics", action="store_true", help="Print streamed glove angles once per second.")
    return parser.parse_args()


async def open_socket(args):
    return await asyncio.wait_for(
        asyncio.open_connection(args.host, args.port),
        timeout=args.connect_timeout,
    )


async def run(args):
    stop_requested = False
    loop = asyncio.get_running_loop()

    def request_stop():
        nonlocal stop_requested
        if not stop_requested:
            print("Stop requested; closing glove socket stream.")
        stop_requested = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    manager = SdkManager.instance()
    devices = manager.scan()
    print(f"Found {len(devices)} glove device(s): {devices}")

    glove = connect_glove(manager, args.glove_name)
    print(f"Connected glove: {args.glove_name}")

    sub = glove.hand_joint_angles().subscribe()
    writer = None

    try:
        _, writer = await open_socket(args)
        await write_message(writer, make_hello_message(args.glove_name))
        print(f"Streaming glove frames to {args.host}:{args.port}")

        interval = 1.0 / args.rate
        deadline = None if args.duration <= 0 else time.monotonic() + args.duration
        last_print = 0.0
        dropped_frames = 0
        seq = 0

        while not stop_requested and (deadline is None or time.monotonic() < deadline):
            try:
                frame, dropped = await recv_latest(sub, timeout=0.2)
            except asyncio.TimeoutError:
                continue

            angles = frame_to_array(frame)
            conf = confidence_array(frame)
            await write_message(writer, make_frame_message(seq, angles, conf))
            seq += 1
            dropped_frames += dropped

            now = time.monotonic()
            if now - last_print >= 1.0:
                msg = f"sent={seq} conf={np.round(conf, 2).tolist()} dropped_glove={dropped_frames}"
                if args.diagnostics:
                    msg += f" glove_f2={np.round(angles[1], 3).tolist()}"
                print(msg)
                last_print = now
                dropped_frames = 0

            await asyncio.sleep(interval)
    finally:
        try:
            sub.close()
        except Exception:
            pass
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
        manager.disconnect_all()
        print("Stopped. Glove disconnected.")


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
