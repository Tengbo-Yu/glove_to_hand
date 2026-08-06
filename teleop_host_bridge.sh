#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Host side: receive one hand's keypoints from RDK, retarget, and forward qpos to robot.
HAND_SIDE="${HAND_SIDE:-right}"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"
ROBOT_HAND_HOST="${ROBOT_HAND_HOST:-127.0.0.1}"
LEFT_RDK_PORT="${LEFT_RDK_PORT:-8865}"
RIGHT_RDK_PORT="${RIGHT_RDK_PORT:-8866}"
LEFT_ROBOT_PORT="${LEFT_ROBOT_PORT:-8765}"
RIGHT_ROBOT_PORT="${RIGHT_ROBOT_PORT:-8767}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
RETARGET_LP_ALPHA="${RETARGET_LP_ALPHA:-0.8}"
RETARGET_NORM_DELTA="${RETARGET_NORM_DELTA:-0.025}"
DEBUG_LATENCY="${DEBUG_LATENCY:-0}"

case "$HAND_SIDE" in
  left)
    LISTEN_PORT="${LISTEN_PORT:-$LEFT_RDK_PORT}"
    ROBOT_HAND_PORT="${ROBOT_HAND_PORT:-$LEFT_ROBOT_PORT}"
    ;;
  right)
    LISTEN_PORT="${LISTEN_PORT:-$RIGHT_RDK_PORT}"
    ROBOT_HAND_PORT="${ROBOT_HAND_PORT:-$RIGHT_ROBOT_PORT}"
    ;;
  *)
    echo "ERROR: HAND_SIDE must be 'left' or 'right', got '$HAND_SIDE'." >&2
    exit 1
    ;;
esac

CMD=(
  conda run --no-capture-output -n "$WUJI_CONDA_ENV" python -u "$SCRIPT_DIR/host_retarget_bridge.py"
  --bind-host 0.0.0.0
  --listen-port "$LISTEN_PORT"
  --robot-host "$ROBOT_HAND_HOST"
  --robot-port "$ROBOT_HAND_PORT"
  --hand "$HAND_SIDE"
  --keep-listening
  --print-every "$PRINT_EVERY"
  --retarget-lp-alpha "$RETARGET_LP_ALPHA"
  --retarget-norm-delta "$RETARGET_NORM_DELTA"
)

if [[ "$DEBUG_LATENCY" == "1" ]]; then
  CMD+=(--debug-latency)
fi

echo "Host retarget bridge: RDK:$LISTEN_PORT -> robot $ROBOT_HAND_HOST:$ROBOT_HAND_PORT ($HAND_SIDE)"
"${CMD[@]}"
