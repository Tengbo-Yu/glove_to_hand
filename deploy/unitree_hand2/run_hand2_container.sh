#!/usr/bin/env bash
set -euo pipefail

side="${1:-}"
if [[ "$side" != "left" && "$side" != "right" ]]; then
  echo "Usage: $0 left|right" >&2
  exit 2
fi
if [[ "${ENABLE_HAND2:-0}" != "1" ]]; then
  echo "Refusing Hand2 hardware mode: set ENABLE_HAND2=1 in /etc/default/wuji-hand2" >&2
  exit 2
fi

case "$side" in
  left)
    port="${LEFT_PORT:-8765}"
    serial="${LEFT_HAND_SN:-}"
    address="${LEFT_HAND_ADDRESS:-192.168.1.110:7447}"
    ;;
  right)
    port="${RIGHT_PORT:-8767}"
    serial="${RIGHT_HAND_SN:-}"
    address="${RIGHT_HAND_ADDRESS:-192.168.1.111:7447}"
    ;;
esac

args=(
  hand_qpos_server.py
  --bind-host 0.0.0.0
  --port "$port"
  --hand "$side"
  --hand-device-name "wuji_hand_2_$side"
  --enable-hand
  --keep-listening
  --control-rate "${CONTROL_RATE:-200}"
  --smooth-tau "${SMOOTH_TAU:-0.02}"
  --max-joint-velocity "${MAX_JOINT_VELOCITY:-6.0}"
  --command-timeout "${COMMAND_TIMEOUT:-1.0}"
  --kp "${KP:-3.5}"
  --kd "${KD:-0.1}"
  --current-limit "${CURRENT_LIMIT:-1.5}"
  --print-every "${PRINT_EVERY:-0.5}"
  --no-home-on-shutdown
)
if [[ -n "$serial" ]]; then
  args+=(--hand-sn "$serial")
elif [[ -n "$address" ]]; then
  args+=(--hand-address "$address")
fi
if [[ "${DEBUG_LATENCY:-0}" == "1" ]]; then
  args+=(--debug-latency)
fi

exec /usr/bin/docker run --rm \
  --name "wuji-hand2-$side" \
  --network host \
  --user 1000:1000 \
  --env HOME=/tmp \
  --env "DATA_COLLECTOR_HOST=${DATA_COLLECTOR_HOST:-127.0.0.1}" \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  "${HAND2_IMAGE:?HAND2_IMAGE is required}" \
  "${args[@]}"
