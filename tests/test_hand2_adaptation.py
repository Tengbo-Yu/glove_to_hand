import unittest
from types import SimpleNamespace

import numpy as np

from hand2_backend import WujiHand2Backend, hand2_device_index_from_nid
from retargeting_hand2 import Hand2RetargetPipeline


def synthetic_open_hand():
    keypoints = np.zeros((21, 3), dtype=np.float64)
    keypoints[1:5] = [
        [0.020, 0.010, 0.0],
        [0.035, 0.025, 0.0],
        [0.050, 0.040, 0.0],
        [0.065, 0.055, 0.0],
    ]
    for base, x in ((5, 0.03), (9, 0.01), (13, -0.01), (17, -0.03)):
        keypoints[base : base + 4] = [
            [x, 0.025, 0.0],
            [x, 0.050, 0.0],
            [x, 0.075, 0.0],
            [x, 0.100, 0.0],
        ]
    return keypoints


class Hand2RetargetPipelineTest(unittest.TestCase):
    def test_right_config_produces_verified_device_order_qpos(self):
        pipeline = Hand2RetargetPipeline(hand_side="right")
        self.assertIn("wuji_hand_2_right", pipeline.config_path.name)
        self.assertEqual(
            pipeline.qpos_permutation.tolist(),
            [16, 17, 18, 19, 0, 1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 8, 9, 10, 11],
        )
        qpos = pipeline.retarget(synthetic_open_hand())
        self.assertEqual(qpos.shape, (20,))
        self.assertTrue(np.isfinite(qpos).all())

    def test_rejects_invalid_keypoints(self):
        pipeline = Hand2RetargetPipeline(hand_side="right")
        with self.assertRaisesRegex(ValueError, "21, 3"):
            pipeline.retarget(np.zeros((20, 3)))


class Hand2JointStateMappingTest(unittest.TestCase):
    NIDS = [1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22, 23, 24]

    def test_bus_nids_map_to_device_order(self):
        self.assertEqual(
            [hand2_device_index_from_nid(nid) for nid in self.NIDS],
            list(range(20)),
        )

    def test_reserved_bus_nids_are_rejected(self):
        for nid in (0, 5, 10, 15, 20, 25):
            with self.subTest(nid=nid), self.assertRaises(ValueError):
                hand2_device_index_from_nid(nid)

    def test_current_positions_uses_bus_nid_mapping(self):
        expected = np.arange(20, dtype=np.float64) + 0.25
        frame = SimpleNamespace(
            joints=[
                SimpleNamespace(nid=nid, position=position)
                for nid, position in zip(self.NIDS, expected)
            ]
        )

        class FakeSubscription:
            def __init__(self):
                self.frame = frame

            def recv(self):
                current, self.frame = self.frame, None
                return current

            def close(self):
                pass

        class FakeHand:
            def joint_states(self):
                return SimpleNamespace(subscribe=FakeSubscription)

        backend = object.__new__(WujiHand2Backend)
        backend._hand = FakeHand()
        np.testing.assert_array_equal(backend.current_positions(), expected)

    def test_read_only_close_does_not_disable_device(self):
        calls = []

        class FakeHand:
            def disable(self):
                calls.append("disable")

            def disconnect(self):
                calls.append("disconnect")

        backend = object.__new__(WujiHand2Backend)
        backend._hand = FakeHand()
        backend._publisher = None
        backend._enabled = False
        backend.close()
        self.assertEqual(calls, ["disconnect"])


if __name__ == "__main__":
    unittest.main()
