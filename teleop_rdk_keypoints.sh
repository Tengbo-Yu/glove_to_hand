#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# RDK side: read one Wuji glove and send raw keypoints to host1.
# Current host1 wired address. Override this if DHCP or the teleop network changes.
HOST_RETARGET_HOST="${HOST_RETARGET_HOST:-10.1.10.166}"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"

HAND_SIDE="${HAND_SIDE:-right}"
LEFT_PORT="${LEFT_PORT:-8865}"
RIGHT_PORT="${RIGHT_PORT:-8866}"
LEFT_GLOVE_NAME="${LEFT_GLOVE_NAME:-glove_l}"
RIGHT_GLOVE_NAME="${RIGHT_GLOVE_NAME:-glove_r}"
LEFT_GLOVE_SN="${LEFT_GLOVE_SN:-WG1JA03260517019}"
RIGHT_GLOVE_SN="${RIGHT_GLOVE_SN:-WG1KA03260512012}"

RATE="${RATE:-120}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
GLOVE_STREAM="${GLOVE_STREAM:-hand_skeleton}"
WUJI_LOG_LEVEL="${WUJI_LOG_LEVEL:-error}"
EMF_RATE_DIVIDER="${EMF_RATE_DIVIDER:-1}"
DEBUG_LATENCY="${DEBUG_LATENCY:-0}"
# The official adapter keeps returning its latest skeleton. Reprocessing that
# cached frame lets the retarget low-pass converge between lower-rate SDK
# updates and avoids visible staircase motion at the Hand 2 output.
SKIP_CACHED_FRAMES="${SKIP_CACHED_FRAMES:-0}"

case "$HAND_SIDE" in
  left)
    HOST_RETARGET_PORT="${HOST_RETARGET_PORT:-$LEFT_PORT}"
    GLOVE_NAME="$LEFT_GLOVE_NAME"
    GLOVE_SN="$LEFT_GLOVE_SN"
    ;;
  right)
    HOST_RETARGET_PORT="${HOST_RETARGET_PORT:-$RIGHT_PORT}"
    GLOVE_NAME="$RIGHT_GLOVE_NAME"
    GLOVE_SN="$RIGHT_GLOVE_SN"
    ;;
  *)
    echo "ERROR: HAND_SIDE must be 'left' or 'right', got '$HAND_SIDE'." >&2
    exit 1
    ;;
esac

CMD=(
  conda run --no-capture-output -n "$WUJI_CONDA_ENV" python -u "$SCRIPT_DIR/glove_qpos_client.py"
  --host "$HOST_RETARGET_HOST"
  --port "$HOST_RETARGET_PORT"
  --hand "$HAND_SIDE"
  --device-name "$GLOVE_NAME"
  --rate "$RATE"
  --print-every "$PRINT_EVERY"
  --glove-stream "$GLOVE_STREAM"
  --emf-rate-divider "$EMF_RATE_DIVIDER"
  --wuji-log-level "$WUJI_LOG_LEVEL"
  --stream-mode keypoints
)

if [[ -n "$GLOVE_SN" ]]; then
  CMD+=(--glove-sn "$GLOVE_SN")
fi
if [[ "$DEBUG_LATENCY" == "1" ]]; then
  CMD+=(--debug-latency)
fi
if [[ "$SKIP_CACHED_FRAMES" == "1" ]]; then
  CMD+=(--skip-cached-frames)
fi

echo "RDK keypoint sender: $HAND_SIDE -> $HOST_RETARGET_HOST:$HOST_RETARGET_PORT"
"${CMD[@]}"
