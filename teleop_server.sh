#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Fill this if multiple hands are connected. Leave empty to let wujihandpy auto-select.
HAND_SERIAL=""

# Port the glove client will connect to. Must match teleop_client.sh.
PORT="8765"

CMD=(
  python "$SCRIPT_DIR/hand_qpos_server.py"
  --bind-host 0.0.0.0
  --port "$PORT"
  --enable-hand
  --rate 60
  --lowpass 10
  --home-duration 2
)

if [[ -n "$HAND_SERIAL" ]]; then
  CMD+=(--hand-serial "$HAND_SERIAL")
fi

"${CMD[@]}"
