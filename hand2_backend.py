"""Safe Wuji Hand 2 hardware backend built on ``wuji_sdk``.

Connecting and reading status never energizes the hand.  Motion is possible only
after an explicit :meth:`enable` call, and commands are accepted only as finite
20-element vectors in Wuji Hand 2 device order (thumb S1..S4 through pinky
S1..S4).
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np


_HAND2_JOINT_NIDS = tuple(
    finger * 5 + segment + 1
    for finger in range(5)
    for segment in range(4)
)
_HAND2_NID_TO_DEVICE_INDEX = {
    nid: index for index, nid in enumerate(_HAND2_JOINT_NIDS)
}


def hand2_device_index_from_nid(nid: int) -> int:
    """Map a Hand 2 bus node ID to thumb-to-pinky device order."""
    normalized_nid = int(nid)
    try:
        return _HAND2_NID_TO_DEVICE_INDEX[normalized_nid]
    except KeyError as exc:
        raise ValueError(f"Unknown Wuji Hand 2 joint nid {normalized_nid}") from exc


class WujiHand2Backend:
    """Network backend for one Wuji Hand 2."""

    _ENABLED_EXT_STATE = 2

    def __init__(
        self,
        hand_side: str,
        *,
        sn: str = "",
        address: str = "",
        device_name: Optional[str] = None,
        kp: float = 3.0,
        kd: float = 0.1,
        current_limit: float = 1.5,
        enable_timeout: float = 5.0,
    ):
        hand_side = hand_side.lower()
        if hand_side not in {"left", "right"}:
            raise ValueError(f"hand_side must be left/right, got {hand_side!r}")
        if sn and address:
            raise ValueError("Specify only one of Hand 2 SN or address")
        if kp < 0 or kd < 0 or current_limit <= 0:
            raise ValueError("kp/kd must be non-negative and current_limit must be positive")

        import wuji_sdk
        from wuji_sdk import DeviceType, SdkManager

        self._sdk = wuji_sdk
        self._DeviceType = DeviceType
        self._manager = SdkManager.instance()
        self.hand_side = hand_side
        self.device_name = device_name or f"wuji_hand_2_{hand_side}"
        self.kp = float(kp)
        self.kd = float(kd)
        self.current_limit = float(current_limit)
        self.enable_timeout = float(enable_timeout)
        self._hand = None
        self._publisher = None
        self._joint_state_sub = None
        self._enabled = False
        self._last_command = None

        try:
            self._hand = self._connect(sn=sn, address=address)
            reported_side = str(self._hand.handedness().get()).lower()
            if reported_side != self.hand_side:
                raise RuntimeError(
                    f"Connected Hand 2 reports {reported_side!r}, expected {self.hand_side!r}"
                )
            online = int(self._hand.online_joints_count().get())
            if online != 20:
                raise RuntimeError(
                    f"Wuji Hand 2 has {online}/20 joints online; refusing motion setup"
                )
            info = getattr(self._hand, "info", None)
            firmware = getattr(info, "firmware_version", "unknown")
            print(
                f"Wuji Hand 2 connected: sn={self._hand.serial_number} "
                f"side={reported_side} firmware={firmware} online={online}/20"
            )
        except BaseException:
            self.close()
            raise

    @property
    def serial_number(self) -> str:
        return str(self._hand.serial_number)

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def _connect(self, *, sn: str, address: str):
        if sn:
            return self._manager.connect(sn=sn, device_name=self.device_name)
        if address:
            return self._manager.connect(address=address, device_name=self.device_name)

        devices = [
            d
            for d in self._manager.scan()
            if d.device_type == self._DeviceType.WujiHand2
            or str(d.sn).upper().startswith("WH2")
        ]
        if not devices:
            raise RuntimeError(
                "No Wuji Hand 2 found. Check power, Ethernet, and the 192.168.1.0/24 route."
            )

        # Current serial convention: character 4 is J=left / K=right.  Use it
        # only to disambiguate discovery; handedness is verified after connect.
        serial_code = "J" if self.hand_side == "left" else "K"
        side_matches = [d for d in devices if len(str(d.sn)) > 3 and str(d.sn)[3].upper() == serial_code]
        if len(side_matches) == 1:
            selected = side_matches[0]
        elif len(devices) == 1:
            selected = devices[0]
        else:
            listing = ", ".join(f"{d.sn}@{d.address}" for d in devices)
            raise RuntimeError(
                f"Cannot uniquely select the {self.hand_side} Hand 2 from: {listing}. "
                "Pass --hand-sn or --hand-address."
            )
        print(f"Discovered Hand 2: {selected.sn} at {selected.address}")
        return self._manager.connect(address=selected.address, device_name=self.device_name)

    @staticmethod
    def _latest_frame(subscription, timeout: float):
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            frame = subscription.recv()
            if frame is not None:
                latest = frame
                while True:
                    newer = subscription.recv()
                    if newer is None:
                        break
                    latest = newer
                return latest
            time.sleep(0.02)
        return latest

    def diagnostics(self, timeout: float = 1.0):
        sub = self._hand.joint_diagnostics().subscribe()
        try:
            return self._latest_frame(sub, timeout)
        finally:
            sub.close()

    def current_positions(self, timeout: float = 1.0) -> np.ndarray:
        sub = self._hand.joint_states().subscribe()
        try:
            frame = self._latest_frame(sub, timeout)
        finally:
            sub.close()
        if frame is None:
            raise TimeoutError("Timed out waiting for Wuji Hand 2 joint_states")
        return self._positions_from_frame(frame)

    @staticmethod
    def _positions_from_frame(frame) -> np.ndarray:
        positions = np.full(20, np.nan, dtype=np.float64)
        seen_indices = set()
        unexpected_nids = []
        duplicate_nids = []
        for entry in frame.joints:
            nid = int(entry.nid)
            try:
                device_index = hand2_device_index_from_nid(nid)
            except ValueError:
                unexpected_nids.append(nid)
                continue
            if device_index in seen_indices:
                duplicate_nids.append(nid)
                continue
            seen_indices.add(device_index)
            positions[device_index] = float(entry.position)
        if unexpected_nids:
            raise RuntimeError(
                f"Unexpected Hand 2 joint-state nids {sorted(set(unexpected_nids))}"
            )
        if duplicate_nids:
            raise RuntimeError(
                f"Duplicate Hand 2 joint-state nids {sorted(set(duplicate_nids))}"
            )
        if not np.isfinite(positions).all():
            missing_indices = np.flatnonzero(~np.isfinite(positions)).tolist()
            missing_nids = [_HAND2_JOINT_NIDS[index] for index in missing_indices]
            raise RuntimeError(
                "Missing or non-finite Hand 2 joint states for "
                f"device indices {missing_indices} (nids {missing_nids})"
            )
        return positions

    def latest_positions(self) -> Optional[np.ndarray]:
        """Return the newest non-blocking joint feedback, or None if unavailable."""
        if getattr(self, "_joint_state_sub", None) is None:
            self._joint_state_sub = self._hand.joint_states().subscribe()
        frame = self._joint_state_sub.recv()
        if frame is None:
            return None
        while True:
            newer = self._joint_state_sub.recv()
            if newer is None:
                break
            frame = newer
        return self._positions_from_frame(frame)

    def _set_with_retry(self, label: str, setter, attempts: int = 3):
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                setter()
                return
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(0.2 * attempt)
        raise RuntimeError(f"Failed to set Hand 2 {label}: {last_error}") from last_error

    def enable(self):
        """Configure MIT control and explicitly energize all 20 joints."""
        if self._enabled:
            return
        self._set_with_retry(
            "effort_limit", lambda: self._hand.effort_limit().set(self.current_limit)
        )
        self._set_with_retry(
            "mit_params", lambda: self._hand.mit_params().set((self.kp, self.kd))
        )
        self._hand.enable()

        deadline = time.monotonic() + self.enable_timeout
        last_frame = None
        sub = self._hand.joint_diagnostics().subscribe()
        try:
            while time.monotonic() < deadline:
                frame = sub.recv()
                if frame is None:
                    time.sleep(0.05)
                    continue
                last_frame = frame
                live = [entry for entry in frame.joints if float(entry.vbus_v_fb) > 0.5]
                if len(live) == 20 and all(
                    int(entry.status_word.ext_state) == self._ENABLED_EXT_STATE
                    for entry in live
                ):
                    break
            else:
                states = [] if last_frame is None else [
                    (entry.nid, int(entry.status_word.ext_state), int(entry.error_code_current))
                    for entry in last_frame.joints
                ]
                raise TimeoutError(
                    f"Hand 2 did not reach Enabled within {self.enable_timeout:g}s: {states}"
                )
        except BaseException:
            try:
                self._hand.disable()
            finally:
                self._enabled = False
            raise
        finally:
            sub.close()

        self._publisher = self._hand.joint_command().publish()
        self._enabled = True
        print(
            f"Wuji Hand 2 enabled: kp={self.kp:g} kd={self.kd:g} "
            f"current_limit={self.current_limit:g}A"
        )

    def send(self, positions):
        if not self._enabled or self._publisher is None:
            raise RuntimeError("Wuji Hand 2 is not enabled")
        positions = np.asarray(positions, dtype=np.float64).reshape(-1)
        if positions.shape != (20,):
            raise ValueError(f"Hand 2 command must contain 20 joints, got {positions.shape}")
        if not np.isfinite(positions).all():
            raise ValueError("Hand 2 command contains NaN or Inf")
        commands = [self._sdk.JointCommand(float(p), 0.0, 0.0) for p in positions]
        self._publisher.send(commands)
        self._last_command = positions.copy()

    def home(self, duration: float = 1.5, rate: float = 60.0):
        if not self._enabled:
            return
        try:
            start = self.current_positions(timeout=1.0)
        except Exception:
            if self._last_command is None:
                raise
            start = self._last_command.copy()
        steps = max(1, int(max(0.0, duration) * rate))
        interval = 1.0 / max(rate, 1.0)
        for step in range(steps):
            alpha = (step + 1) / steps
            self.send((1.0 - alpha) * start)
            time.sleep(interval)
        self.send(np.zeros(20, dtype=np.float64))

    def close(self):
        joint_state_sub, self._joint_state_sub = (
            getattr(self, "_joint_state_sub", None),
            None,
        )
        if joint_state_sub is not None:
            try:
                joint_state_sub.close()
            except Exception:
                pass
        publisher, self._publisher = self._publisher, None
        if publisher is not None:
            try:
                publisher.close()
            except Exception:
                pass
        hand, self._hand = self._hand, None
        if hand is not None:
            # Only undo enablement performed by this backend.  A read-only
            # client must not disable a hand owned by Studio or another process.
            if self._enabled:
                try:
                    hand.disable()
                except Exception:
                    pass
            try:
                hand.disconnect()
            except Exception:
                try:
                    self._manager.disconnect(device_name=self.device_name)
                except Exception:
                    pass
        self._enabled = False
