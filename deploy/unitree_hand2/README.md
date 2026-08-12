# Unitree dual Wuji Hand2 service

Target-specific deployment for `unitree@192.168.123.164`.

完整的构建、部署、验收、运行、停机和回滚步骤见
[`UNITREE_HAND2_SERVICE_SOP.md`](UNITREE_HAND2_SERVICE_SOP.md)。

## Runtime layout

- `wuji-hand2-network.service` first requires the normal management address
  `192.168.123.164/24` to exist on `eth0`, then adds `192.168.1.100/32` and
  direct `/32` routes only for the factory Hand2 addresses `.110` and `.111`.
  It allows up to 60 seconds for the management address to appear after boot,
  then fails closed if it is still absent. It never replaces or removes that
  address.
- `wuji-hand2@left.service` listens on TCP `8765`.
- `wuji-hand2@right.service` listens on TCP `8767`.
- Both services use the offline ARM64 image named in `/etc/default/wuji-hand2`.
- The field-validated sender path is RDK X5 `192.168.112.230` through the
  `Delta` Wi-Fi connection to Unitree `wlan0` (DHCP address
  `192.168.112.106` during the 2026-08-12 acceptance).  Re-check this DHCP
  address after reboot instead of treating it as static.

The service process is ready at boot, but the hardware remains disabled until
that side receives its first valid, non-zero, side-matched command frame.  A
hello-only connection is not enough to enable the hand.  A one-second command
watchdog disables the hand when fresh commands stop.

## Current target mapping

- left: `WH2JA01260717002`, `192.168.1.110:7447`
- right: `WH2KA01260730030`, `192.168.1.111:7447`
- `KP=3.5`, `KD=0.1`, current limit `1.5 A`
- control `200 Hz`, smoothing `0.02 s`, slew limit `6 rad/s`

## Operations

```bash
sudo systemctl status wuji-hand2-network.service \
  wuji-hand2@left.service wuji-hand2@right.service
ss -lnt '( sport = :8765 or sport = :8767 )'

# Stop RDK command clients first, then stop the robot-side services.
sudo systemctl stop wuji-hand2@left.service wuji-hand2@right.service

sudo systemctl start wuji-hand2-network.service \
  wuji-hand2@left.service wuji-hand2@right.service

journalctl -fu wuji-hand2@left.service
journalctl -fu wuji-hand2@right.service
```

Do not run another Wuji Studio/control process for the same hand while this
service owns it.  Keep the mechanical workspace clear before starting an RDK
sender.  The 2026-08-12 field run verified both sides, continuous keypoint
traffic, physical control, sender disconnect and Hand2 disable; DataCollector
traffic was deliberately disabled and remains separately unverified.

## Offline image inputs

`requirements-runtime-arm64.txt` is the pinned Python 3.10 ARM64 wheel set.
Run `prepare_model_assets.sh <build-context>/model-assets` to fetch only the
Hand2 URDF, MJCF and mesh assets from Wuji description commit
`7d547ad50ca8cff92d999ae2cc01fc69bcb7c2b6`.  The Docker build is intentionally
run with `--network none` after those inputs are staged.
