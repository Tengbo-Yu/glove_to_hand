import socket
import tempfile
import unittest
from pathlib import Path

import msgpack
import numpy as np
from mcap.writer import Writer

from qpos_protocol import SocketLineReader, decode_message
from wuji_mcap_replay_client import (
    ReplayFrame,
    iter_replay_frames,
    parse_args,
    playback_sleep_seconds,
    schedule_replay_frames,
    send_replay_frame,
)


def write_mcap(path, records):
    with Path(path).open("wb") as output:
        writer = Writer(output)
        writer.start()
        channels = {}
        for record in records:
            topic = record["topic"]
            if topic not in channels:
                channels[topic] = writer.register_channel(
                    topic=topic, message_encoding="msgpack", schema_id=0
                )
            writer.add_message(
                channel_id=channels[topic],
                log_time=record["log_time"],
                publish_time=record["publish_time"],
                sequence=record["sequence"],
                data=msgpack.packb(record["payload"], use_bin_type=True),
            )
        writer.finish()


class ReplayClientTest(unittest.TestCase):
    def test_reads_hand2_command_and_apply_time(self):
        qpos = np.arange(20, dtype=np.float64).reshape(5, 4)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "episode.mcap"
            write_mcap(
                path,
                [
                    {
                        "topic": "/wuji/hand/right/command",
                        "log_time": 10,
                        "publish_time": 20,
                        "sequence": 4,
                        "payload": {
                            "hand_side": "right",
                            "glove_seq": 7,
                            "received_qpos_5x4": qpos.tolist(),
                            "apply_timestamp_ns": 30,
                        },
                    }
                ],
            )
            frames = list(iter_replay_frames(path, timing="apply_time"))
        self.assertEqual(frames[0].sequence, 7)
        self.assertEqual(frames[0].timestamp_ns, 30)
        np.testing.assert_array_equal(frames[0].qpos, qpos)

    def test_scheduling_and_gap_cap(self):
        frames = [
            ReplayFrame("left", 1, 1_000_000_000, np.zeros((5, 4)), "left"),
            ReplayFrame("right", 2, 1_600_000_000, np.ones((5, 4)), "right"),
            ReplayFrame("left", 3, 2_600_000_000, np.ones((5, 4)), "left"),
        ]
        scheduled = schedule_replay_frames(frames, start_sec=0.5, duration_sec=1.0)
        self.assertEqual([frame.sequence for frame in scheduled], [2])
        self.assertAlmostEqual(scheduled[0].replay_time_sec, 0.1)
        self.assertAlmostEqual(
            playback_sleep_seconds(0, 1, speed=2, max_gap_sec=0.25), 0.25
        )

    def test_send_uses_hand2_v2_device_order_protocol(self):
        server, client = socket.socketpair()
        try:
            frame = ReplayFrame(
                "left", 42, 12_500_000_000, np.zeros((5, 4)), "topic"
            )
            send_replay_frame(client, frame)
            decoded = decode_message(SocketLineReader(server).read_line())
            self.assertEqual(decoded["protocol"], "glove-qpos-v2")
            self.assertEqual(decoded["hand_side"], "left")
            self.assertEqual(decoded["joint_order"], "device")
        finally:
            server.close()
            client.close()

    def test_current_right_server_port_is_8767(self):
        args = parse_args(["episode.mcap", "--dry-run"])
        self.assertEqual(args.left_port, 8765)
        self.assertEqual(args.right_port, 8767)


if __name__ == "__main__":
    unittest.main()
