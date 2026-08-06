#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Recommended single-host path: receive RDK keypoints, retarget, and drive
# Hand 2 in one process. This replaces teleop_host_bridge.sh +
# teleop_robot_hand.sh when host1 and robot are the same machine.
HAND_SIDE="${HAND_SIDE:-right}"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"
RDK_BIND_HOST="${RDK_BIND_HOST:-10.1.10.166}"
LEFT_PORT="${LEFT_PORT:-8865}"
RIGHT_PORT="${RIGHT_PORT:-8866}"
LEFT_HAND_SN="${LEFT_HAND_SN:-}"
RIGHT_HAND_SN="${RIGHT_HAND_SN:-WH2KA01260730030}"
LEFT_HAND_ADDRESS="${LEFT_HAND_ADDRESS:-}"
RIGHT_HAND_ADDRESS="${RIGHT_HAND_ADDRESS:-}"
CONTROL_RATE="${CONTROL_RATE:-200}"
SMOOTH_TAU="${SMOOTH_TAU:-0.02}"
MAX_JOINT_VELOCITY="${MAX_JOINT_VELOCITY:-6.0}"
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
    DEFAULT_CONFIG="$SCRIPT_DIR/wuji-retargeting/example/config/adaptive_analytical_wuji_glove_wuji_hand_2_left.yaml"
    ;;
  right)
    PORT="${PORT:-$RIGHT_PORT}"
    HAND_SN="$RIGHT_HAND_SN"
    HAND_ADDRESS="$RIGHT_HAND_ADDRESS"
    DEFAULT_CONFIG="$SCRIPT_DIR/config/hand2_right_teleop.yaml"
    ;;
  *)
    echo "ERROR: HAND_SIDE must be 'left' or 'right', got '$HAND_SIDE'." >&2
    exit 1
    ;;
esac

RETARGET_CONFIG="${RETARGET_CONFIG:-$DEFAULT_CONFIG}"

CMD=(
  conda run --no-capture-output -n "$WUJI_CONDA_ENV" python -u "$SCRIPT_DIR/hand_qpos_server.py"
  --bind-host "$RDK_BIND_HOST"
  --port "$PORT"
  --hand "$HAND_SIDE"
  --config "$RETARGET_CONFIG"
  --enable-hand
  --keep-listening
  --control-rate "$CONTROL_RATE"
  --smooth-tau "$SMOOTH_TAU"
  --max-joint-velocity "$MAX_JOINT_VELOCITY"
  --command-timeout "$COMMAND_TIMEOUT"
  --kp "$KP"
  --kd "$KD"
  --current-limit "$CURRENT_LIMIT"
  --print-every "$PRINT_EVERY"
  --no-home-on-shutdown
)

if [[ -n "$HAND_SN" ]]; then
  CMD+=(--hand-sn "$HAND_SN")
fi
if [[ -n "$HAND_ADDRESS" ]]; then
  CMD+=(--hand-address "$HAND_ADDRESS")
fi
if [[ "$DEBUG_LATENCY" == "1" ]]; then
  CMD+=(--debug-latency)
fi

echo "Direct Hand 2 teleop: RDK -> $RDK_BIND_HOST:$PORT -> $HAND_SIDE Hand 2"
echo "Hand: sn=${HAND_SN:-auto} address=${HAND_ADDRESS:-auto}"
echo "Retarget config: $RETARGET_CONFIG"
echo "Response: ${CONTROL_RATE}Hz, tau=${SMOOTH_TAU}s, max ${MAX_JOINT_VELOCITY}rad/s"
"${CMD[@]}"
