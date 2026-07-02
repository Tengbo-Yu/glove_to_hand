#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# IP address of the machine running teleop_server.sh. Use 127.0.0.1 when both run locally.
HAND_SERVER_HOST="10.1.10.166"
HAND_SERVER_PORT="8765"

# Glove/hand side and wuji-sdk device name.
HAND_SIDE="left"
GLOVE_NAME="glove"

# Fill this if multiple Wuji gloves are online. Leave empty to auto-connect.
# RIGHT_GLOVE_SN="WG1KA03260512012"
# LEFT_GLOVE_SN="WG1JA03260517019"
GLOVE_SN="WG1JA03260517019"

# Set DEBUG_LATENCY=1 to print stage timing instead of only qpos summaries.
DEBUG_LATENCY="${DEBUG_LATENCY:-0}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
# Use offline_hand_skeleton to avoid subscribing to the SDK hand_skeleton background handler.
GLOVE_STREAM="${GLOVE_STREAM:-offline_hand_skeleton}"
WUJI_LOG_LEVEL="${WUJI_LOG_LEVEL:-error}"

CMD=(
  python "$SCRIPT_DIR/glove_qpos_client.py"
  --host "$HAND_SERVER_HOST"
  --port "$HAND_SERVER_PORT"
  --hand "$HAND_SIDE"
  --device-name "$GLOVE_NAME"
  --rate 20
  --print-every "$PRINT_EVERY"
  --glove-stream "$GLOVE_STREAM"
  --wuji-log-level "$WUJI_LOG_LEVEL"
)

if [[ -n "$GLOVE_SN" ]]; then
  CMD+=(--glove-sn "$GLOVE_SN")
fi

if [[ "$DEBUG_LATENCY" == "1" ]]; then
  CMD+=(--debug-latency)
fi

"${CMD[@]}"
