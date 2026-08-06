#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"
if [[ "${ENABLE_HAND2:-0}" != "1" ]]; then
  echo "Refusing to energize Hand 2. Re-run with ENABLE_HAND2=1." >&2
  exit 2
fi

# Robot side: receive verified device-order qpos and drive one network Hand 2.
HAND_SIDE="${HAND_SIDE:-right}"
ROBOT_BIND_HOST="${ROBOT_BIND_HOST:-127.0.0.1}"
LEFT_PORT="${LEFT_PORT:-8765}"
RIGHT_PORT="${RIGHT_PORT:-8767}"
LEFT_HAND_SN="${LEFT_HAND_SN:-}"
RIGHT_HAND_SN="${RIGHT_HAND_SN:-WH2KA01260730030}"
LEFT_HAND_ADDRESS="${LEFT_HAND_ADDRESS:-}"
RIGHT_HAND_ADDRESS="${RIGHT_HAND_ADDRESS:-}"
CONTROL_RATE="${CONTROL_RATE:-60}"
SMOOTH_TAU="${SMOOTH_TAU:-0.05}"
MAX_JOINT_VELOCITY="${MAX_JOINT_VELOCITY:-2.0}"
COMMAND_TIMEOUT="${COMMAND_TIMEOUT:-1.0}"
KP="${KP:-3.0}"
KD="${KD:-0.1}"
CURRENT_LIMIT="${CURRENT_LIMIT:-1.5}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
DEBUG_LATENCY="${DEBUG_LATENCY:-0}"

case "$HAND_SIDE" in
  left)
    PORT="${PORT:-$LEFT_PORT}"
    HAND_SN="$LEFT_HAND_SN"
    HAND_ADDRESS="$LEFT_HAND_ADDRESS"
    ;;
  right)
    PORT="${PORT:-$RIGHT_PORT}"
    HAND_SN="$RIGHT_HAND_SN"
    HAND_ADDRESS="$RIGHT_HAND_ADDRESS"
    ;;
  *)
    echo "ERROR: HAND_SIDE must be 'left' or 'right', got '$HAND_SIDE'." >&2
    exit 1
    ;;
esac

CMD=(
  conda run --no-capture-output -n "$WUJI_CONDA_ENV" python -u "$SCRIPT_DIR/hand_qpos_server.py"
  --bind-host "$ROBOT_BIND_HOST"
  --port "$PORT"
  --hand "$HAND_SIDE"
  --enable-hand
  --keep-listening
  --control-rate "$CONTROL_RATE"
  --smooth-tau "$SMOOTH_TAU"
  --max-joint-velocity "$MAX_JOINT_VELOCITY"
  --command-timeout "$COMMAND_TIMEOUT"
  --kp "$KP"
  --kd "$KD"
  --current-limit "$CURRENT_LIMIT"
  --no-home-on-shutdown
)

if [[ -n "$HAND_SN" ]]; then
  CMD+=(--hand-sn "$HAND_SN")
fi
if [[ -n "$HAND_ADDRESS" ]]; then
  CMD+=(--hand-address "$HAND_ADDRESS")
fi
if [[ "$DEBUG_LATENCY" == "1" ]]; then
  CMD+=(--debug-latency --print-every "$PRINT_EVERY")
fi

echo "Robot Hand 2 server: $HAND_SIDE bind=$ROBOT_BIND_HOST:$PORT sn=${HAND_SN:-auto} address=${HAND_ADDRESS:-auto}"
"${CMD[@]}"
