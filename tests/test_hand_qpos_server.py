import socket
import time
import unittest

import numpy as np

from hand_qpos_server import read_latest_qpos
from qpos_protocol import (
    SocketLineReader,
    decode_message,
    encode_message,
    make_hello_message,
    make_keypoints_message,
    make_qpos_message,
)


class ReadLatestQposTest(unittest.TestCase):
    def test_returns_buffered_latest_frame_without_waiting_for_socket_timeout(self):
        server_sock, client_sock = socket.socketpair()
        try:
            server_sock.settimeout(0.2)
            reader = SocketLineReader(server_sock)

            client_sock.sendall(encode_message(make_hello_message("left", time.time())))
            for seq in range(3):
                qpos = np.full((5, 4), seq, dtype=np.float64)
                client_sock.sendall(encode_message(make_qpos_message(seq, qpos, time.time())))

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
                client_sock.sendall(encode_message(make_qpos_message(seq, qpos, time.time())))

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
            debug={"client_retarget_ms": 4.2, "client_sdk_drained": 3},
        )
        decoded = decode_message(encode_message(message))

        self.assertEqual(decoded["seq"], 7)
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


if __name__ == "__main__":
    unittest.main()
