#!/usr/bin/env bash
set -euo pipefail

# Change this to the IP address of the machine running teleop_hand_server.sh.
HAND_SERVER_HOST="127.0.0.1"
HAND_SERVER_PORT="8765"

# Change this to match the device name assigned by wuji-sdk auto_connect.
GLOVE_NAME="glove_0"

conda run -n wuji python /home/user/workspace/wuji/glove_client.py \
  --host "$HAND_SERVER_HOST" \
  --port "$HAND_SERVER_PORT" \
  --glove-name "$GLOVE_NAME" \
  --duration 100 \
  --rate 60 \
  --diagnostics
