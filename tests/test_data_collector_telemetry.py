import json
import unittest

import numpy as np

from data_collector_telemetry import (
    DataCollectorTelemetryPublisher,
    build_glove_command_payload,
    qpos_fields,
)


class FakeAgain(Exception):
    pass


class FakeSocket:
    def __init__(self, error=None):
        self.error = error
        self.sent = []
        self.connected = []
        self.options = []

    def setsockopt(self, option, value):
        self.options.append((option, value))

    def connect(self, endpoint):
        self.connected.append(endpoint)

    def send_multipart(self, parts, flags=0):
        if self.error is not None:
            raise self.error
        self.sent.append((parts, flags))


class DataCollectorTelemetryPublisherTest(unittest.TestCase):
    def test_publishes_header_and_packed_payload_as_multipart(self):
        socket = FakeSocket()
        publisher = DataCollectorTelemetryPublisher(
            endpoint="tcp://127.0.0.1:6011",
            source="wuji_left_glove_command",
            frame_id="wuji_left_glove_command",
            socket=socket,
            packer=lambda payload: json.dumps(payload, sort_keys=True).encode("utf-8"),
            again_exception=FakeAgain,
            noblock_flag=99,
        )

        sent = publisher.publish(
            sequence=7,
            payload={"hand_side": "left", "value": [1, 2, 3]},
            source_timestamp_ns=123456789,
        )

        self.assertTrue(sent)
        self.assertEqual(publisher.dropped_count, 0)
        self.assertEqual(len(socket.sent), 1)
        parts, flags = socket.sent[0]
        self.assertEqual(flags, 99)
        self.assertEqual(len(parts), 2)
        header = json.loads(parts[0].decode("utf-8"))
        self.assertEqual(header["sequence"], 7)
        self.assertEqual(header["source_timestamp_ns"], 123456789)
        self.assertEqual(header["source"], "wuji_left_glove_command")
        self.assertEqual(header["encoding"], "msgpack")
        self.assertEqual(header["frame_id"], "wuji_left_glove_command")
        self.assertIn(b'"hand_side": "left"', parts[1])

    def test_nonblocking_send_drop_is_counted_without_raising(self):
        socket = FakeSocket(error=FakeAgain())
        publisher = DataCollectorTelemetryPublisher(
            endpoint="tcp://127.0.0.1:6011",
            source="wuji_left_glove_command",
            frame_id="wuji_left_glove_command",
            socket=socket,
            packer=lambda payload: b"payload",
            again_exception=FakeAgain,
            noblock_flag=99,
        )

        sent = publisher.publish(
            sequence=8,
            payload={"hand_side": "left"},
            source_timestamp_ns=123456790,
        )

        self.assertFalse(sent)
        self.assertEqual(publisher.dropped_count, 1)

    def test_glove_command_payload_keeps_5x4_and_flat20_qpos(self):
        qpos = np.arange(20, dtype=np.float64).reshape(5, 4)
        fingers_pose = np.arange(63, dtype=np.float64).reshape(21, 3)

        payload = build_glove_command_payload(
            hand_side="left",
            seq=4,
            qpos=qpos,
            fingers_pose=fingers_pose,
            config_path="/tmp/left.yaml",
            glove_device_name="glove_l",
            glove_sn="WGLEFT",
            tcp_target="192.168.123.164:8765",
            protocol="glove-qpos-v1",
            telemetry_dropped_count=3,
        )

        self.assertEqual(payload["retargeted_qpos_5x4"], qpos.tolist())
        self.assertEqual(payload["retargeted_qpos_flat20"], qpos.reshape(-1).tolist())
        self.assertEqual(payload["fingers_pose"], fingers_pose.tolist())
        self.assertEqual(payload["fingers_pose_shape"], [21, 3])
        self.assertEqual(payload["joint_names"], ["MCP_aa", "MCP_fe", "PIP", "DIP"])
        self.assertEqual(payload["finger_names"], ["thumb", "index", "middle", "ring", "pinky"])

    def test_qpos_fields_rejects_non_5x4_input(self):
        with self.assertRaises(ValueError):
            qpos_fields("bad_qpos", np.zeros((4, 5), dtype=np.float64))


if __name__ == "__main__":
    unittest.main()
