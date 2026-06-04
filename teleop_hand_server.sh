#!/usr/bin/env bash
set -euo pipefail

# Fill this if multiple hands are connected. Leave empty to let wujihandpy auto-select.
HAND_SERIAL=""

CMD=(
  conda run -n wuji python /home/user/workspace/wuji/hand_server.py
  --bind-host 0.0.0.0
  --port 8765
  --enable-hand
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

if [[ -n "$HAND_SERIAL" ]]; then
  CMD+=(--hand-serial "$HAND_SERIAL")
fi

"${CMD[@]}"
