#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# IP address of the machine running teleop_server.sh. Use 127.0.0.1 when both run locally.
HAND_SERVER_HOST="127.0.0.1"
HAND_SERVER_PORT="8765"

# Glove/hand side and wuji-sdk device name.
HAND_SIDE="right"
GLOVE_NAME="glove"

# Fill this if multiple Wuji gloves are online. Leave empty to auto-connect.
GLOVE_SN=""

CMD=(
  conda run -n wuji python "$SCRIPT_DIR/glove_qpos_client.py"
  --host "$HAND_SERVER_HOST"
  --port "$HAND_SERVER_PORT"
  --hand "$HAND_SIDE"
  --device-name "$GLOVE_NAME"
  --rate 60
)

if [[ -n "$GLOVE_SN" ]]; then
  CMD+=(--glove-sn "$GLOVE_SN")
fi

"${CMD[@]}"
