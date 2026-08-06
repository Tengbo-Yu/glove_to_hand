"""Wuji Hand 2 retargeting setup and verified device-order conversion."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent
RETARGETING_ROOT = PROJECT_ROOT / "wuji-retargeting"
RETARGETING_EXAMPLE = RETARGETING_ROOT / "example"

for _path in (RETARGETING_ROOT, RETARGETING_EXAMPLE):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)


def resolve_hand2_config(config, hand_side: str) -> Path:
    if config:
        return Path(config).expanduser().resolve()
    return (
        RETARGETING_EXAMPLE
        / "config"
        / f"adaptive_analytical_wuji_glove_wuji_hand_2_{hand_side}.yaml"
    ).resolve()


class Hand2RetargetPipeline:
    """Retarget MediaPipe points and fail closed unless joint order is verified."""

    def __init__(self, config=None, hand_side: str = "right"):
        from wuji_retargeting import Retargeter
        from utils.config_paths import (
            mjcf_joint_order,
            qpos_reorder_perm,
            resolve_mjcf_path,
        )

        self.hand_side = hand_side.lower()
        self.config_path = resolve_hand2_config(config, self.hand_side)
        if not self.config_path.is_file():
            raise FileNotFoundError(
                f"Hand 2 retargeting config/model not found: {self.config_path}. "
                "Run: git -C wuji-retargeting submodule update --init --recursive"
            )
        self.retargeter = Retargeter.from_yaml(str(self.config_path), self.hand_side)
        mjcf_path = resolve_mjcf_path(self.config_path)
        if not mjcf_path or not Path(mjcf_path).is_file():
            raise FileNotFoundError(
                f"Hand 2 MJCF is missing for {self.config_path}: {mjcf_path}. "
                "Initialize wuji-retargeting submodules."
            )
        self.qpos_permutation = qpos_reorder_perm(
            self.retargeter.optimizer.robot.dof_joint_names,
            mjcf_joint_order(mjcf_path),
        )
        if self.qpos_permutation is None:
            raise ValueError(
                "Cannot align Hand 2 URDF joint order with MJCF/device order; "
                "refusing to produce hardware commands."
            )
        if self.qpos_permutation.shape != (20,):
            raise ValueError(
                f"Expected a 20-joint Hand 2 permutation, got {self.qpos_permutation.shape}"
            )
        print(
            "Hand 2 qpos remap active (URDF -> device): "
            f"{self.qpos_permutation.tolist()}"
        )

    def retarget(self, keypoints) -> np.ndarray:
        keypoints = np.asarray(keypoints, dtype=np.float64)
        if keypoints.shape != (21, 3) or not np.isfinite(keypoints).all():
            raise ValueError("Retargeting input must be finite MediaPipe keypoints (21, 3)")
        qpos = np.asarray(self.retargeter.retarget(keypoints), dtype=np.float64).reshape(-1)
        if qpos.shape != (20,) or not np.isfinite(qpos).all():
            raise ValueError(f"Retargeter produced invalid Hand 2 qpos {qpos.shape}")
        return qpos[self.qpos_permutation]

