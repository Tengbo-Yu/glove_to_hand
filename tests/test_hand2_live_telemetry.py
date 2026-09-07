"""Exercise the field telemetry path without SDK or hardware access."""
import socket
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from hand_qpos_server import parse_args, serve_connection
from qpos_protocol import encode_message, make_qpos_message


class FieldTelemetryTest(unittest.TestCase):
    def test_single_target_keeps_sampling_but_stale_feedback_is_not_published(self):
        with patch("sys.argv", ["hand_qpos_server.py", "--enable-hand",
                                "--duration", "0.25", "--control-rate", "200",
                                "--socket-timeout", "0.01"]):
            args = parse_args()
        pipeline = SimpleNamespace(
            config_path="fake.yaml",
            retargeter=SimpleNamespace(lp_filter=SimpleNamespace(alpha=0.2),
                                      optimizer=SimpleNamespace(norm_delta=0.04)),
        )
        backend = Mock(serial_number="TEST", is_enabled=True)
        backend.current_positions.return_value = np.zeros(20)
        feedback = [np.zeros(20)]
        backend.latest_positions.side_effect = lambda: feedback.pop() if feedback else None
        command = Mock(dropped_count=0)
        state = Mock(dropped_count=0)
        server, client = socket.socketpair()
        try:
            client.sendall(encode_message(make_qpos_message(
                17, np.ones((5, 4)) * 0.1, time.time(), "right")))
            with (patch("hand_qpos_server.Hand2RetargetPipeline", return_value=pipeline),
                  patch("hand_qpos_server.WujiHand2Backend", return_value=backend),
                  patch("hand_qpos_server.create_hand_telemetry_publishers",
                        return_value=(command, state))):
                serve_connection(server, ("local", 0), args, lambda: False)
        finally:
            client.close()
            server.close()
        samples = [call.kwargs for call in command.publish.call_args_list]
        self.assertGreaterEqual(len(samples), 3)
        self.assertLessEqual(len(samples), 10)
        self.assertEqual([s["sequence"] for s in samples], list(range(len(samples))))
        self.assertTrue(samples[0]["payload"]["target_updated"])
        self.assertTrue(all(not s["payload"]["target_updated"] for s in samples[1:]))
        self.assertGreater(samples[-1]["payload"]["target_age_ms"], 0)
        state.publish.assert_called_once()
        self.assertTrue(state.publish.call_args.kwargs["payload"]["feedback_fresh"])
        self.assertEqual(state.publish.call_args.kwargs["payload"]["glove_seq"], 17)
        self.assertGreater(backend.send.call_count, len(samples))
