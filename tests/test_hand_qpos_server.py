import socket
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from hand_qpos_server import QposSmoother, read_latest_qpos, serve_connection
from qpos_protocol import (
    SocketLineReader,
    decode_message,
    encode_message,
    make_hello_message,
    make_keypoints_message,
    make_qpos_message,
)


class QposSmootherTest(unittest.TestCase):
    def test_first_target_is_rate_limited_from_measured_position(self):
        smoother = QposSmoother(tau=0.0, max_velocity=2.0)
        measured = np.zeros((5, 4), dtype=np.float64)
        smoother.initialize(measured)
        smoother.set_target(np.ones((5, 4), dtype=np.float64))

        output = smoother.step(0.1)

        np.testing.assert_allclose(output, 0.2)

    def test_initialize_rejects_invalid_joint_shape(self):
        smoother = QposSmoother()
        with self.assertRaisesRegex(ValueError, "shape"):
            smoother.initialize(np.zeros(20, dtype=np.float64))


class EnableGateTest(unittest.TestCase):
    def test_hello_without_command_never_connects_or_enables_hand(self):
        server_sock, client_sock = socket.socketpair()
        client_sock.sendall(encode_message(make_hello_message("right", time.time())))
        client_sock.close()
        args = SimpleNamespace(
            socket_timeout=0.02,
            config=None,
            hand="right",
            retarget_lp_alpha=0.0,
            debug_latency=False,
            enable_hand=True,
        )
        fake_pipeline = SimpleNamespace(
            config_path="fake-hand2.yaml",
            retargeter=SimpleNamespace(),
        )

        with (
            patch("hand_qpos_server.Hand2RetargetPipeline", return_value=fake_pipeline),
            patch("hand_qpos_server.WujiHand2Backend") as backend_class,
        ):
            serve_connection(server_sock, ("local", 0), args, lambda: False)

        backend_class.assert_not_called()


class ReadLatestQposTest(unittest.TestCase):
    def test_returns_buffered_latest_frame_without_waiting_for_socket_timeout(self):
        server_sock, client_sock = socket.socketpair()
        try:
            server_sock.settimeout(0.2)
            reader = SocketLineReader(server_sock)

            client_sock.sendall(encode_message(make_hello_message("left", time.time())))
            for seq in range(3):
                qpos = np.full((5, 4), seq, dtype=np.float64)
                client_sock.sendall(
                    encode_message(make_qpos_message(seq, qpos, time.time(), "left"))
                )

            start = time.monotonic()
            message, dropped = read_latest_qpos(reader)
            elapsed = time.monotonic() - start

            self.assertLess(elapsed, 0.05)
            self.assertEqual(message["seq"], 2)
            self.assertEqual(dropped, 2)
        finally:
            server_sock.close()
            client_sock.close()

    def test_records_optional_read_metrics(self):
        server_sock, client_sock = socket.socketpair()
        try:
            server_sock.settimeout(0.2)
            reader = SocketLineReader(server_sock)

            for seq in range(2):
                qpos = np.full((5, 4), seq, dtype=np.float64)
                client_sock.sendall(
                    encode_message(make_qpos_message(seq, qpos, time.time(), "left"))
                )

            metrics = {}
            message, dropped = read_latest_qpos(reader, metrics)

            self.assertEqual(message["seq"], 1)
            self.assertEqual(dropped, 1)
            self.assertEqual(metrics["decoded_frames"], 2)
            self.assertIn("socket_wait_ms", metrics)
            self.assertIn("socket_drain_ms", metrics)
        finally:
            server_sock.close()
            client_sock.close()


class ProtocolDebugTest(unittest.TestCase):
    def test_qpos_debug_metadata_round_trips(self):
        qpos = np.ones((5, 4), dtype=np.float64)
        message = make_qpos_message(
            7,
            qpos,
            123.0,
            "right",
            debug={"client_retarget_ms": 4.2, "client_sdk_drained": 3},
        )
        decoded = decode_message(encode_message(message))

        self.assertEqual(decoded["seq"], 7)
        self.assertEqual(decoded["hand_side"], "right")
        self.assertEqual(decoded["joint_order"], "device")
        self.assertEqual(decoded["debug"]["client_retarget_ms"], 4.2)
        self.assertEqual(decoded["debug"]["client_sdk_drained"], 3)
    def test_keypoints_metadata_round_trips(self):
        keypoints = np.ones((21, 3), dtype=np.float64)
        message = make_keypoints_message(
            8,
            keypoints,
            456.0,
            "left",
            debug={"client_glove_ms": 0.2},
        )
        decoded = decode_message(encode_message(message))

        self.assertEqual(decoded["type"], "keypoints_frame")
        self.assertEqual(decoded["seq"], 8)
        self.assertEqual(decoded["hand_side"], "left")
        self.assertEqual(decoded["keypoints"].shape, (21, 3))
        self.assertEqual(decoded["debug"]["client_glove_ms"], 0.2)

    def test_rejects_qpos_without_verified_device_order(self):
        qpos = np.zeros((5, 4), dtype=np.float64)
        message = make_qpos_message(1, qpos, time.time(), "right")
        message["joint_order"] = "urdf"
        with self.assertRaisesRegex(ValueError, "joint_order='device'"):
            decode_message(encode_message(message))

    def test_rejects_nonfinite_qpos(self):
        qpos = np.zeros((5, 4), dtype=np.float64)
        qpos[0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            make_qpos_message(1, qpos, time.time(), "right")


if __name__ == "__main__":
    unittest.main()
