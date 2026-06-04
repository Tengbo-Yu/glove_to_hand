import argparse
import json
import unittest

import numpy as np

from glove_to_hand_common import (
    JOINT_MATRIX_SHAPE,
    PROTOCOL_NAME,
    decode_message,
    make_frame_message,
    make_hello_message,
    parse_joint_matrix,
)


class GloveToHandCommonTest(unittest.TestCase):
    def test_parse_joint_matrix_uses_default_for_missing_value(self):
        matrix = parse_joint_matrix(None, 0.7)

        self.assertEqual(matrix.shape, JOINT_MATRIX_SHAPE)
        np.testing.assert_allclose(matrix, np.full(JOINT_MATRIX_SHAPE, 0.7))

    def test_parse_joint_matrix_requires_twenty_values(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_joint_matrix("1,2,3", 0.7)

    def test_frame_message_round_trips_arrays(self):
        angles = np.arange(20, dtype=np.float64).reshape(JOINT_MATRIX_SHAPE)
        confidence = np.linspace(0.1, 0.9, 5)

        message = make_frame_message(12, angles, confidence, timestamp=123.5)
        decoded = decode_message(json.dumps(message))

        self.assertEqual(decoded["type"], "frame")
        self.assertEqual(decoded["protocol"], PROTOCOL_NAME)
        self.assertEqual(decoded["seq"], 12)
        self.assertEqual(decoded["timestamp"], 123.5)
        np.testing.assert_allclose(decoded["angles"], angles)
        np.testing.assert_allclose(decoded["confidence"], confidence)

    def test_hello_message_round_trips(self):
        decoded = decode_message(json.dumps(make_hello_message("glove_0")))

        self.assertEqual(decoded["type"], "hello")
        self.assertEqual(decoded["protocol"], PROTOCOL_NAME)
        self.assertEqual(decoded["glove_name"], "glove_0")

    def test_rejects_wrong_protocol(self):
        with self.assertRaises(ValueError):
            decode_message('{"type":"frame","protocol":"wrong"}')


if __name__ == "__main__":
    unittest.main()
