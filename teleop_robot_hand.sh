#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Robot side: receive retargeted qpos from the host and drive the Wuji Hand.
HAND_SIDE="${HAND_SIDE:-left}"
LEFT_PORT="${LEFT_PORT:-8765}"
RIGHT_PORT="${RIGHT_PORT:-8766}"
LEFT_HAND_SERIAL="${LEFT_HAND_SERIAL:-3378387C3233}"
RIGHT_HAND_SERIAL="${RIGHT_HAND_SERIAL:-3378387E3233}"
CONTROL_RATE="${CONTROL_RATE:-60}"
LOWPASS="${LOWPASS:-15}"
SMOOTH_TAU="${SMOOTH_TAU:-0.05}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
DEBUG_LATENCY="${DEBUG_LATENCY:-0}"

case "$HAND_SIDE" in
  left)
    PORT="${PORT:-$LEFT_PORT}"
    HAND_SERIAL="$LEFT_HAND_SERIAL"
    ;;
  right)
    PORT="${PORT:-$RIGHT_PORT}"
    HAND_SERIAL="$RIGHT_HAND_SERIAL"
    ;;
  *)
    echo "ERROR: HAND_SIDE must be 'left' or 'right', got '$HAND_SIDE'." >&2
    exit 1
    ;;
esac

CMD=(
  python "$SCRIPT_DIR/hand_qpos_server.py"
  --bind-host 0.0.0.0
  --port "$PORT"
  --hand "$HAND_SIDE"
  --enable-hand
  --keep-listening
  --rate 60
  --control-rate "$CONTROL_RATE"
  --lowpass "$LOWPASS"
  --smooth-tau "$SMOOTH_TAU"
  --home-duration 2
)

if [[ -n "$HAND_SERIAL" ]]; then
  CMD+=(--hand-serial "$HAND_SERIAL")
fi
if [[ "$DEBUG_LATENCY" == "1" ]]; then
  CMD+=(--debug-latency --print-every "$PRINT_EVERY")
fi

echo "Robot hand qpos server: $HAND_SIDE on port $PORT serial=$HAND_SERIAL"
"${CMD[@]}"
