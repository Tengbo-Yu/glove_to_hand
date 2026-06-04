#!/usr/bin/env bash
set -euo pipefail

# Fill these if multiple hands are connected. Leave empty to let wujihandpy auto-select.
LEFT_HAND_SERIAL=""
RIGHT_HAND_SERIAL=""

# Change these to match the device names assigned by wuji-sdk auto_connect.
LEFT_GLOVE_NAME="glove_l"
RIGHT_GLOVE_NAME="glove_r"

CMD=(
  conda run -n wuji python /home/user/workspace/wuji/glove_to_hand_dual.py
  --enable-hand
  --left-glove-name "$LEFT_GLOVE_NAME"
  --right-glove-name "$RIGHT_GLOVE_NAME"
  --duration 100
  --rate 60
  --lowpass 10
  --gain -1.5
  --max-delta 1.2
  --joint-gains "-1.5,-1.5,-1.5,-1.5, -1.5,-0.7,-1.5,-1.5, -1.5,-0.7,-1.5,-1.5, -1.5,-0.1,-1.5,-1.5, -1.5,-0.1,-1.5,-1.5"
  --joint-max-deltas "1.2,1.2,1.2,1.2, 1.2,1.0,1.2,1.2, 1.2,1.0,1.2,1.2, 1.2,0.1,1.2,1.2, 1.2,0.1,1.2,1.2"
  --invert-side-sway
  --max-velocity 3.0
  --confidence-threshold 0.3
  --home-duration 5
  --diagnostics
)

if [[ -n "$LEFT_HAND_SERIAL" ]]; then
  CMD+=(--left-hand-serial "$LEFT_HAND_SERIAL")
fi

if [[ -n "$RIGHT_HAND_SERIAL" ]]; then
  CMD+=(--right-hand-serial "$RIGHT_HAND_SERIAL")
fi

"${CMD[@]}"
