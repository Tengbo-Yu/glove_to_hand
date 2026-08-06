#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"

# Single-hand wired teleop client.
# Teleop data goes over the dedicated Ethernet link only; SSH/Wi-Fi stays on 10.1.10.x.
# Local wired IP:  192.168.126.10/24
# Robot wired IP:  192.168.126.20/24
# Robot side should have no default gateway on this wired interface.

WIRED_IFACE="${WIRED_IFACE:-enx00e04c584b78}"
LOCAL_WIRED_IP="${LOCAL_WIRED_IP:-192.168.126.10/24}"
HAND_SERVER_HOST="${HAND_SERVER_HOST:-192.168.126.20}"

# Select one hand. Override with: HAND_SIDE=left bash teleop_local_wired.sh
HAND_SIDE="${HAND_SIDE:-right}"

LEFT_PORT="${LEFT_PORT:-8765}"
RIGHT_PORT="${RIGHT_PORT:-8766}"
LEFT_GLOVE_NAME="${LEFT_GLOVE_NAME:-glove_l}"
RIGHT_GLOVE_NAME="${RIGHT_GLOVE_NAME:-glove_r}"
LEFT_GLOVE_SN="${LEFT_GLOVE_SN:-WG1JA03260517019}"
RIGHT_GLOVE_SN="${RIGHT_GLOVE_SN:-WG1KA03260512012}"

DEBUG_LATENCY="${DEBUG_LATENCY:-0}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
GLOVE_STREAM="${GLOVE_STREAM:-offline_hand_skeleton}"
WUJI_LOG_LEVEL="${WUJI_LOG_LEVEL:-error}"
RATE="${RATE:-60}"
STREAM_MODE="${STREAM_MODE:-keypoints}"

configure_local_wired_ip() {
  if [[ ! -e "/sys/class/net/$WIRED_IFACE" ]]; then
    echo "ERROR: wired interface $WIRED_IFACE does not exist." >&2
    echo "Set WIRED_IFACE=<interface>, for example: WIRED_IFACE=eth0 bash $0" >&2
    exit 1
  fi

  sudo ip link set "$WIRED_IFACE" up
  if ! ip -brief addr show dev "$WIRED_IFACE" | grep -q "${LOCAL_WIRED_IP%/*}"; then
    echo "Configuring $WIRED_IFACE with $LOCAL_WIRED_IP. You may be prompted for sudo." >&2
    sudo ip addr add "$LOCAL_WIRED_IP" dev "$WIRED_IFACE"
  fi
}

check_wired_route() {
  local host="$1"
  local route
  route="$(ip route get "$host" 2>/dev/null || true)"
  if [[ -z "$route" ]]; then
    echo "ERROR: no route to $host. Check the wired IP configuration." >&2
    exit 1
  fi
  if [[ "$route" != *" dev $WIRED_IFACE "* ]]; then
    echo "ERROR: route to $host is not using $WIRED_IFACE:" >&2
    echo "  $route" >&2
    echo "Set WIRED_IFACE=<interface> or HAND_SERVER_HOST=<robot-wired-ip>." >&2
    exit 1
  fi
  echo "Wired route OK: $route"
}

case "$HAND_SIDE" in
  left)
    HAND_SERVER_PORT="$LEFT_PORT"
    GLOVE_NAME="$LEFT_GLOVE_NAME"
    GLOVE_SN="$LEFT_GLOVE_SN"
    ;;
  right)
    HAND_SERVER_PORT="$RIGHT_PORT"
    GLOVE_NAME="$RIGHT_GLOVE_NAME"
    GLOVE_SN="$RIGHT_GLOVE_SN"
    ;;
  *)
    echo "ERROR: HAND_SIDE must be 'left' or 'right', got '$HAND_SIDE'." >&2
    exit 1
    ;;
esac

configure_local_wired_ip
check_wired_route "$HAND_SERVER_HOST"

echo "Config: single-hand wired teleop"
echo "Stream mode: $STREAM_MODE"
echo "Hand side: $HAND_SIDE"
echo "Glove device name: $GLOVE_NAME"
echo "Glove stream: $GLOVE_STREAM"
echo "Wuji SDK log level: $WUJI_LOG_LEVEL"
echo "Hand server: $HAND_SERVER_HOST:$HAND_SERVER_PORT"
echo "Wired interface: $WIRED_IFACE"

CMD=(
    conda run -n "$WUJI_CONDA_ENV" python "$PROJECT_ROOT/glove_qpos_client.py"
  --host "$HAND_SERVER_HOST"
  --port "$HAND_SERVER_PORT"
  --hand "$HAND_SIDE"
  --device-name "$GLOVE_NAME"
  --rate "$RATE"
  --print-every "$PRINT_EVERY"
  --glove-stream "$GLOVE_STREAM"
  --wuji-log-level "$WUJI_LOG_LEVEL"
  --stream-mode "$STREAM_MODE"
)

if [[ -n "$GLOVE_SN" ]]; then
  CMD+=(--glove-sn "$GLOVE_SN")
fi
if [[ "$DEBUG_LATENCY" == "1" ]]; then
  CMD+=(--debug-latency)
fi

"${CMD[@]}"
