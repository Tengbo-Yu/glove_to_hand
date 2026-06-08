import socket
import time
import unittest

import numpy as np

from hand_qpos_server import build_hand_state_payload, publish_hand_command, read_latest_qpos
from qpos_protocol import SocketLineReader, encode_message, make_hello_message, make_qpos_message


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


class FakePublisher:
    def __init__(self):
        self.dropped_count = 2
        self.published = []

    def publish(self, sequence, payload, source_timestamp_ns=None):
        self.published.append((sequence, payload, source_timestamp_ns))
        return True


class FakeController:
    def __init__(self, effort_error=None):
        self.position = np.arange(20, dtype=np.float64).reshape(5, 4)
        self.effort = np.arange(20, 40, dtype=np.float64).reshape(5, 4)
        self.effort_error = effort_error

    def get_joint_actual_position(self):
        return self.position

    def get_joint_actual_effort(self):
        if self.effort_error is not None:
            raise self.effort_error
        return self.effort


class HandTelemetryPayloadTest(unittest.TestCase):
    def test_publish_hand_command_records_received_and_applied_command(self):
        publisher = FakePublisher()
        qpos = np.arange(20, dtype=np.float64).reshape(5, 4)
        message = {
            "seq": 12,
            "timestamp": 10.5,
            "qpos": qpos,
        }

        publish_hand_command(
            publisher=publisher,
            hand_side="right",
            message=message,
            peer=("192.168.123.10", 4567),
            dropped_socket=1,
            dropped_socket_total=5,
            enable_hand=True,
            applied_to_controller=True,
            apply_timestamp_ns=123456789,
        )

        self.assertEqual(len(publisher.published), 1)
        sequence, payload, source_timestamp_ns = publisher.published[0]
        self.assertEqual(sequence, 12)
        self.assertEqual(source_timestamp_ns, 10_500_000_000)
        self.assertEqual(payload["hand_side"], "right")
        self.assertEqual(payload["glove_seq"], 12)
        self.assertEqual(payload["glove_timestamp"], 10.5)
        self.assertEqual(payload["received_qpos_5x4"], qpos.tolist())
        self.assertEqual(payload["received_qpos_flat20"], qpos.reshape(-1).tolist())
        self.assertEqual(payload["tcp_peer"], "192.168.123.10:4567")
        self.assertEqual(payload["dropped_socket"], 1)
        self.assertEqual(payload["dropped_socket_total"], 5)
        self.assertTrue(payload["enable_hand"])
        self.assertTrue(payload["applied_to_controller"])
        self.assertEqual(payload["apply_timestamp_ns"], 123456789)
        self.assertEqual(payload["telemetry_dropped_count"], 2)

    def test_build_hand_state_payload_records_position_and_effort(self):
        target = np.arange(20, dtype=np.float64).reshape(5, 4)
        controller = FakeController()

        payload = build_hand_state_payload(
            hand_side="left",
            controller=controller,
            target_qpos=target,
            hand_serial="3378387C3233",
            lowpass=10.0,
            enable_upstream=True,
        )

        self.assertTrue(payload["state_available"])
        self.assertIsNone(payload["read_error"])
        self.assertTrue(payload["effort_supported"])
        self.assertEqual(payload["target_qpos_5x4"], target.tolist())
        self.assertEqual(payload["actual_qpos_5x4"], controller.position.tolist())
        self.assertEqual(payload["actual_effort_5x4"], controller.effort.tolist())
        self.assertEqual(payload["hand_serial"], "3378387C3233")
        self.assertEqual(payload["lowpass"], 10.0)
        self.assertTrue(payload["enable_upstream"])

    def test_build_hand_state_payload_tolerates_missing_effort_api(self):
        target = np.arange(20, dtype=np.float64).reshape(5, 4)
        controller = FakeController(effort_error=RuntimeError("effort unsupported"))

        payload = build_hand_state_payload(
            hand_side="right",
            controller=controller,
            target_qpos=target,
            hand_serial=None,
            lowpass=5.0,
            enable_upstream=True,
        )

        self.assertTrue(payload["state_available"])
        self.assertFalse(payload["effort_supported"])
        self.assertIsNone(payload["actual_effort_5x4"])
        self.assertEqual(payload["read_error"], "effort unsupported")


if __name__ == "__main__":
    unittest.main()
