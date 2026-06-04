#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Fill these if multiple hands are connected. Leave empty to let wujihandpy auto-select.
LEFT_HAND_SERIAL=""
RIGHT_HAND_SERIAL=""

# Fill these if multiple Wuji gloves are online. Leave empty to auto-connect a single glove per name.
LEFT_GLOVE_SN=""
RIGHT_GLOVE_SN=""

# Change these to match the device names assigned by wuji-sdk auto_connect.
LEFT_GLOVE_NAME="glove_l"
RIGHT_GLOVE_NAME="glove_r"

CMD=(
  conda run -n wuji python "$SCRIPT_DIR/glove_to_hand_dual.py"
  --enable-hand
  --left-glove-name "$LEFT_GLOVE_NAME"
  --right-glove-name "$RIGHT_GLOVE_NAME"
  --rate 60
  --lowpass 10
  --home-duration 2
)

if [[ -n "$LEFT_HAND_SERIAL" ]]; then
  CMD+=(--left-hand-serial "$LEFT_HAND_SERIAL")
fi

if [[ -n "$RIGHT_HAND_SERIAL" ]]; then
  CMD+=(--right-hand-serial "$RIGHT_HAND_SERIAL")
fi

if [[ -n "$LEFT_GLOVE_SN" ]]; then
  CMD+=(--left-glove-sn "$LEFT_GLOVE_SN")
fi

if [[ -n "$RIGHT_GLOVE_SN" ]]; then
  CMD+=(--right-glove-sn "$RIGHT_GLOVE_SN")
fi

"${CMD[@]}"
