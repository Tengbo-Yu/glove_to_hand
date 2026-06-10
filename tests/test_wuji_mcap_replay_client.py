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
    playback_sleep_seconds,
    schedule_replay_frames,
    send_replay_frame,
)


LEFT_HAND_COMMAND_TOPIC = "/wuji/hand/left/command"
RIGHT_HAND_COMMAND_TOPIC = "/wuji/hand/right/command"


def _write_mcap(path, records):
    with Path(path).open("wb") as output:
        writer = Writer(output)
        writer.start()
        channels = {}
        for record in records:
            topic = record["topic"]
            if topic not in channels:
                channels[topic] = writer.register_channel(
                    topic=topic,
                    message_encoding="msgpack",
                    schema_id=0,
                    metadata={},
                )
            writer.add_message(
                channel_id=channels[topic],
                log_time=record["log_time"],
                publish_time=record["publish_time"],
                sequence=record.get("sequence", 0),
                data=msgpack.packb(record["payload"], use_bin_type=True),
            )
        writer.finish()


class WujiMcapReplayClientTest(unittest.TestCase):
    def test_reads_left_and_right_hand_command_frames_from_mcap(self):
        left_qpos = np.arange(20, dtype=np.float64).reshape(5, 4)
        right_qpos = (np.arange(20, dtype=np.float64) + 100).reshape(5, 4)

        with tempfile.TemporaryDirectory() as tmp:
            mcap_path = Path(tmp) / "episode.mcap"
            _write_mcap(
                mcap_path,
                [
                    {
                        "topic": RIGHT_HAND_COMMAND_TOPIC,
                        "log_time": 3_000_000_000,
                        "publish_time": 30_000_000_000,
                        "sequence": 22,
                        "payload": {
                            "hand_side": "right",
                            "glove_seq": 22,
                            "glove_timestamp": 30.0,
                            "received_qpos_5x4": right_qpos.tolist(),
                            "apply_timestamp_ns": 33_000_000_000,
                        },
                    },
                    {
                        "topic": LEFT_HAND_COMMAND_TOPIC,
                        "log_time": 1_000_000_000,
                        "publish_time": 10_000_000_000,
                        "sequence": 11,
                        "payload": {
                            "hand_side": "left",
                            "glove_seq": 11,
                            "glove_timestamp": 10.0,
                            "received_qpos_5x4": left_qpos.tolist(),
                            "apply_timestamp_ns": 12_000_000_000,
                        },
                    },
                ],
            )

            frames = list(iter_replay_frames(mcap_path, source="hand_command", timing="log_time"))

        self.assertEqual([frame.hand_side for frame in frames], ["left", "right"])
        self.assertEqual([frame.sequence for frame in frames], [11, 22])
        self.assertEqual([frame.timestamp_ns for frame in frames], [1_000_000_000, 3_000_000_000])
        np.testing.assert_allclose(frames[0].qpos, left_qpos)
        np.testing.assert_allclose(frames[1].qpos, right_qpos)

    def test_apply_time_timing_uses_payload_apply_timestamp(self):
        qpos = np.zeros((5, 4), dtype=np.float64)

        with tempfile.TemporaryDirectory() as tmp:
            mcap_path = Path(tmp) / "episode.mcap"
            _write_mcap(
                mcap_path,
                [
                    {
                        "topic": LEFT_HAND_COMMAND_TOPIC,
                        "log_time": 1,
                        "publish_time": 2,
                        "sequence": 1,
                        "payload": {
                            "hand_side": "left",
                            "glove_seq": 1,
                            "received_qpos_5x4": qpos.tolist(),
                            "apply_timestamp_ns": 123,
                        },
                    }
                ],
            )

            frames = list(iter_replay_frames(mcap_path, timing="apply_time"))

        self.assertEqual(frames[0].timestamp_ns, 123)

    def test_rejects_hand_command_with_non_5x4_qpos(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcap_path = Path(tmp) / "episode.mcap"
            _write_mcap(
                mcap_path,
                [
                    {
                        "topic": LEFT_HAND_COMMAND_TOPIC,
                        "log_time": 1,
                        "publish_time": 1,
                        "sequence": 1,
                        "payload": {
                            "hand_side": "left",
                            "glove_seq": 1,
                            "received_qpos_5x4": np.zeros((4, 5), dtype=np.float64).tolist(),
                        },
                    }
                ],
            )

            with self.assertRaisesRegex(ValueError, "received_qpos_5x4"):
                list(iter_replay_frames(mcap_path))

    def test_schedule_filters_start_and_duration_relative_to_first_frame(self):
        frames = [
            ReplayFrame("left", 1, 1_000_000_000, np.zeros((5, 4)), LEFT_HAND_COMMAND_TOPIC),
            ReplayFrame("right", 2, 1_600_000_000, np.ones((5, 4)), RIGHT_HAND_COMMAND_TOPIC),
            ReplayFrame("left", 3, 2_600_000_000, np.ones((5, 4)) * 2, LEFT_HAND_COMMAND_TOPIC),
        ]

        scheduled = schedule_replay_frames(frames, start_sec=0.5, duration_sec=1.0)

        self.assertEqual([item.sequence for item in scheduled], [2])
        self.assertAlmostEqual(scheduled[0].replay_time_sec, 0.1)

    def test_playback_sleep_respects_speed_and_max_gap(self):
        self.assertAlmostEqual(playback_sleep_seconds(0.0, 1.0, speed=2.0, max_gap_sec=None), 0.5)
        self.assertAlmostEqual(playback_sleep_seconds(0.0, 10.0, speed=1.0, max_gap_sec=0.25), 0.25)
        self.assertAlmostEqual(playback_sleep_seconds(1.0, 0.5, speed=1.0, max_gap_sec=None), 0.0)

    def test_send_replay_frame_writes_glove_qpos_protocol_message(self):
        server_sock, client_sock = socket.socketpair()
        try:
            frame = ReplayFrame(
                hand_side="left",
                sequence=42,
                timestamp_ns=12_500_000_000,
                qpos=np.arange(20, dtype=np.float64).reshape(5, 4),
                topic=LEFT_HAND_COMMAND_TOPIC,
            )

            send_replay_frame(client_sock, frame)
            decoded = decode_message(SocketLineReader(server_sock).read_line())

            self.assertEqual(decoded["type"], "frame")
            self.assertEqual(decoded["seq"], 42)
            self.assertEqual(decoded["timestamp"], 12.5)
            np.testing.assert_allclose(decoded["qpos"], frame.qpos)
        finally:
            server_sock.close()
            client_sock.close()


if __name__ == "__main__":
    unittest.main()
