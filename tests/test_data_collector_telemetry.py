import json
import unittest

import numpy as np

from data_collector_telemetry import (
    DataCollectorTelemetryPublisher,
    build_glove_command_payload,
    default_endpoint,
    qpos_fields,
)


class FakeAgain(Exception):
    pass


class FakeSocket:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    def send_multipart(self, parts, flags=0):
        if self.error is not None:
            raise self.error
        self.sent.append((parts, flags))

    def close(self, linger=0):
        pass


class DataCollectorTelemetryTest(unittest.TestCase):
    def test_hammerhead_ports_are_preserved(self):
        self.assertEqual(default_endpoint("glove_command", "left", "host"), "tcp://host:6011")
        self.assertEqual(default_endpoint("glove_command", "right", "host"), "tcp://host:6012")
        self.assertEqual(default_endpoint("hand_command", "left", "host"), "tcp://host:6013")
        self.assertEqual(default_endpoint("hand_command", "right", "host"), "tcp://host:6014")
        self.assertEqual(default_endpoint("hand_state", "left", "host"), "tcp://host:6015")
        self.assertEqual(default_endpoint("hand_state", "right", "host"), "tcp://host:6016")

    def test_publisher_sends_hammerhead_multipart_contract(self):
        sock = FakeSocket()
        publisher = DataCollectorTelemetryPublisher(
            endpoint="tcp://127.0.0.1:6011",
            source="wuji_left_glove_command",
            frame_id="wuji_left_glove_command",
            socket=sock,
            packer=lambda value: json.dumps(value).encode(),
            again_exception=FakeAgain,
            noblock_flag=123,
        )
        self.assertTrue(
            publisher.publish(
                sequence=7,
                source_timestamp_ns=99,
                payload={"hand_side": "left"},
            )
        )
        parts, flags = sock.sent[0]
        header = json.loads(parts[0])
        self.assertEqual(flags, 123)
        self.assertEqual(header["sequence"], 7)
        self.assertEqual(header["source_timestamp_ns"], 99)
        self.assertEqual(header["encoding"], "msgpack")
        self.assertEqual(len(parts), 2)

    def test_nonblocking_drop_is_counted(self):
        publisher = DataCollectorTelemetryPublisher(
            endpoint="unused",
            source="source",
            frame_id="frame",
            socket=FakeSocket(FakeAgain()),
            packer=lambda value: b"payload",
            again_exception=FakeAgain,
        )
        self.assertFalse(publisher.publish(sequence=1, payload={}))
        self.assertEqual(publisher.dropped_count, 1)

    def test_keypoint_mode_keeps_raw_input_and_marks_server_retarget(self):
        keypoints = np.arange(63, dtype=np.float64).reshape(21, 3)
        payload = build_glove_command_payload(
            hand_side="right",
            seq=3,
            keypoints=keypoints,
            stream_mode="keypoints",
            retargeted_qpos=None,
            retarget_config=None,
            glove_device_name="glove_r",
            glove_sn="WG1K",
            glove_stream="hand_skeleton",
            tcp_target="10.1.10.166:8866",
            protocol="glove-qpos-v2",
            cached_frame=False,
            telemetry_dropped_count=0,
        )
        self.assertEqual(payload["schema"], "wuji_glove_command.hand2.v2")
        self.assertEqual(payload["glove_keypoints_21x3"], keypoints.tolist())
        self.assertEqual(payload["retarget_location"], "hand_server")
        self.assertIsNone(payload["retargeted_qpos_5x4"])

    def test_qpos_shape_and_finite_values_are_enforced(self):
        self.assertEqual(len(qpos_fields("q", np.zeros((5, 4)))["q_flat20"]), 20)
        with self.assertRaisesRegex(ValueError, "shape"):
            qpos_fields("q", np.zeros((4, 5)))
        bad = np.zeros((5, 4))
        bad[0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            qpos_fields("q", bad)


if __name__ == "__main__":
    unittest.main()
