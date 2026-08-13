import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from hand_qpos_server import (
    LatestRetargetWorker,
    QposSmoother,
    build_hand_command_payload,
    build_hand_state_payload,
    read_latest_qpos,
    serve_connection,
)
from qpos_protocol import (
    SocketLineReader,
    decode_message,
    encode_message,
    make_hello_message,
    make_ack_message,
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


class LatestRetargetWorkerTest(unittest.TestCase):
    def test_keeps_only_latest_pending_frame(self):
        first_started = threading.Event()
        release_first = threading.Event()
        processed = []

        def retarget(keypoints):
            keypoints = np.asarray(keypoints, dtype=np.float64)
            seq = int(keypoints[0, 0])
            processed.append(seq)
            if seq == 1:
                first_started.set()
                release_first.wait(timeout=1.0)
            return np.full(20, seq, dtype=np.float64)

        pipeline = SimpleNamespace(
            retarget=retarget,
            retargeter=SimpleNamespace(),
        )
        worker = LatestRetargetWorker(pipeline)
        worker.start()

        def item(seq):
            return {
                "message": make_keypoints_message(
                    seq,
                    np.full((21, 3), seq, dtype=np.float64),
                    time.time(),
                    "right",
                ),
                "frame_start_perf": time.perf_counter(),
            }

        try:
            self.assertFalse(worker.submit(item(1)))
            self.assertTrue(first_started.wait(timeout=1.0))
            self.assertFalse(worker.submit(item(2)))
            self.assertTrue(worker.submit(item(3)))
            release_first.set()

            deadline = time.monotonic() + 1.0
            results = []
            while time.monotonic() < deadline and (not results or processed[-1] != 3):
                result = worker.take_result()
                if result is not None:
                    results.append(result)
                time.sleep(0.005)
            result = worker.take_result()
            if result is not None:
                results.append(result)

            self.assertEqual(processed, [1, 3])
            self.assertEqual(results[-1]["message"]["seq"], 3)
            np.testing.assert_array_equal(results[-1]["qpos"], 3.0)
        finally:
            release_first.set()
            worker.stop()


class Hand2TelemetryPayloadTest(unittest.TestCase):
    def test_command_preserves_replay_field_and_applied_command(self):
        target = np.arange(20, dtype=np.float64).reshape(5, 4)
        applied = target / 2
        message = make_keypoints_message(
            17, np.ones((21, 3)), 123.5, "right"
        )
        payload = build_hand_command_payload(
            hand_side="right",
            message=message,
            target_qpos=target,
            applied_qpos=applied,
            peer=("10.1.10.20", 4567),
            dropped_socket=2,
            dropped_socket_total=5,
            seq_gap=1,
            enable_hand=True,
            applied_to_hand=True,
            apply_timestamp_ns=999,
            server_command_timestamp_ns=998,
            retarget_config="hand2.yaml",
            telemetry_dropped_count=0,
        )
        self.assertEqual(payload["schema"], "wuji_hand_command.hand2.v2")
        self.assertEqual(payload["received_qpos_5x4"], target.tolist())
        self.assertEqual(payload["applied_qpos_5x4"], applied.tolist())
        self.assertEqual(payload["glove_keypoints_21x3"], message["keypoints"])
        self.assertEqual(payload["joint_order"], "hand2_device_thumb_to_pinky")
        self.assertEqual(payload["server_command_timestamp_ns"], 998)

    def test_state_reports_retained_feedback_freshness(self):
        qpos = np.zeros((5, 4), dtype=np.float64)
        payload = build_hand_state_payload(
            hand_side="left",
            target_qpos=qpos,
            applied_qpos=qpos,
            actual_qpos=qpos,
            actual_timestamp_ns=123,
            feedback_fresh=False,
            hand_serial="WH2J",
            kp=3.5,
            kd=0.1,
            current_limit=1.5,
            enable_hand=True,
            telemetry_dropped_count=0,
        )
        self.assertTrue(payload["state_available"])
        self.assertFalse(payload["feedback_fresh"])
        self.assertEqual(payload["actual_qpos_flat20"], [0.0] * 20)
        self.assertFalse(payload["effort_supported"])


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


class LatencyAckConnectionTest(unittest.TestCase):
    def test_debug_connection_acks_after_retarget(self):
        server_sock, client_sock = socket.socketpair()
        args = SimpleNamespace(
            socket_timeout=0.05,
            config=None,
            hand="right",
            retarget_lp_alpha=0.0,
            retarget_norm_delta=None,
            debug_latency=True,
            enable_hand=False,
            control_rate=200.0,
            duration=0.25,
            disable_output_smoothing=True,
            smooth_tau=0.0,
            max_joint_velocity=0.0,
            command_timeout=1.0,
            print_every=10.0,
            debug_slow_ms=100.0,
        )
        fake_retargeter = SimpleNamespace(
            lp_filter=SimpleNamespace(alpha=0.2),
            optimizer=SimpleNamespace(norm_delta=0.04),
        )
        fake_pipeline = SimpleNamespace(
            config_path="fake-hand2.yaml",
            retargeter=fake_retargeter,
            retarget=lambda keypoints: np.zeros(20, dtype=np.float64),
        )
        try:
            client_sock.settimeout(1.0)
            with patch(
                "hand_qpos_server.Hand2RetargetPipeline",
                return_value=fake_pipeline,
            ):
                server_thread = threading.Thread(
                    target=serve_connection,
                    args=(server_sock, ("local", 0), args, lambda: False),
                    daemon=True,
                )
                server_thread.start()
                probe = time.perf_counter()
                client_sock.sendall(
                    encode_message(
                        make_keypoints_message(
                            11,
                            np.ones((21, 3), dtype=np.float64),
                            time.time(),
                            "right",
                            debug={
                                "request_ack": True,
                                "client_probe_perf": probe,
                            },
                        )
                    )
                )
                ack = decode_message(SocketLineReader(client_sock).read_line())

                self.assertEqual(ack["type"], "ack")
                self.assertEqual(ack["seq"], 11)
                self.assertEqual(ack["client_probe_perf"], probe)
                self.assertGreaterEqual(ack["server_queue_ms"], 0.0)
                self.assertGreaterEqual(ack["server_frame_ms"], ack["server_retarget_ms"])
                server_thread.join(timeout=1.0)
                self.assertFalse(server_thread.is_alive())
        finally:
            client_sock.close()
            server_sock.close()

    def test_command_timeout_ends_session_without_server_exception(self):
        server_sock, client_sock = socket.socketpair()
        args = SimpleNamespace(
            socket_timeout=0.02,
            config=None,
            hand="right",
            retarget_lp_alpha=0.0,
            retarget_norm_delta=None,
            retarget_maxeval=None,
            debug_latency=False,
            enable_hand=True,
            hand_sn="WH2KTEST",
            hand_address="",
            hand_device_name="test-right",
            kp=3.5,
            kd=0.1,
            current_limit=1.5,
            enable_timeout=0.1,
            control_rate=200.0,
            duration=0.5,
            disable_output_smoothing=True,
            smooth_tau=0.0,
            max_joint_velocity=0.0,
            command_timeout=0.05,
            print_every=10.0,
            debug_slow_ms=100.0,
            home_on_shutdown=False,
            home_duration=0.0,
            rate=60.0,
        )
        fake_pipeline = SimpleNamespace(
            config_path="fake-hand2.yaml",
            retargeter=SimpleNamespace(
                lp_filter=SimpleNamespace(alpha=0.2),
                optimizer=SimpleNamespace(norm_delta=0.04),
            ),
            retarget=lambda keypoints: np.zeros(20, dtype=np.float64),
        )
        fake_backend = SimpleNamespace(
            is_enabled=True,
            serial_number="WH2KTEST",
            enable=lambda: None,
            current_positions=lambda: np.zeros(20, dtype=np.float64),
            send=lambda qpos: None,
            latest_positions=lambda: None,
            close=lambda: None,
        )

        try:
            client_sock.sendall(
                encode_message(
                    make_qpos_message(
                        1,
                        np.zeros((5, 4), dtype=np.float64),
                        time.time(),
                        "right",
                    )
                )
            )
            with (
                patch(
                    "hand_qpos_server.Hand2RetargetPipeline",
                    return_value=fake_pipeline,
                ),
                patch(
                    "hand_qpos_server.WujiHand2Backend",
                    return_value=fake_backend,
                ),
            ):
                start = time.monotonic()
                serve_connection(server_sock, ("local", 0), args, lambda: False)
                elapsed = time.monotonic() - start
            self.assertLess(elapsed, 0.3)
        finally:
            client_sock.close()
            server_sock.close()


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
    def test_latency_ack_round_trips(self):
        message = make_ack_message(
            seq=9,
            client_probe_perf=1234.5,
            server_queue_ms=1.2,
            server_frame_ms=3.4,
            server_retarget_ms=2.8,
        )
        decoded = decode_message(encode_message(message))

        self.assertEqual(decoded["type"], "ack")
        self.assertEqual(decoded["seq"], 9)
        self.assertEqual(decoded["client_probe_perf"], 1234.5)
        self.assertEqual(decoded["server_queue_ms"], 1.2)
        self.assertEqual(decoded["server_frame_ms"], 3.4)
        self.assertEqual(decoded["server_retarget_ms"], 2.8)

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
