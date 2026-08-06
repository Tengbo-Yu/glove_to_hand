#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# RDK side: read both Wuji gloves and send raw keypoints to the host retarget bridge.
HOST_RETARGET_HOST="${HOST_RETARGET_HOST:-192.168.126.20}"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"
LEFT_PORT="${LEFT_PORT:-8865}"
RIGHT_PORT="${RIGHT_PORT:-8866}"
LEFT_GLOVE_NAME="${LEFT_GLOVE_NAME:-glove_l}"
RIGHT_GLOVE_NAME="${RIGHT_GLOVE_NAME:-glove_r}"
LEFT_GLOVE_SN="${LEFT_GLOVE_SN:-WG1JA03260517019}"
RIGHT_GLOVE_SN="${RIGHT_GLOVE_SN:-WG1KA03260512012}"

RATE="${RATE:-60}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
GLOVE_STREAM="${GLOVE_STREAM:-offline_hand_skeleton}"
WUJI_LOG_LEVEL="${WUJI_LOG_LEVEL:-error}"
DEBUG_LATENCY="${DEBUG_LATENCY:-0}"

pids=()
cleanup() {
  trap - INT TERM
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

start_sender() {
  local side="$1"
  local port="$2"
  local name="$3"
  local sn="$4"
  local cmd=(
    conda run -n "$WUJI_CONDA_ENV" python "$SCRIPT_DIR/glove_qpos_client.py"
    --host "$HOST_RETARGET_HOST"
    --port "$port"
    --hand "$side"
    --device-name "$name"
    --rate "$RATE"
    --print-every "$PRINT_EVERY"
    --glove-stream "$GLOVE_STREAM"
    --wuji-log-level "$WUJI_LOG_LEVEL"
    --stream-mode keypoints
  )
  if [[ -n "$sn" ]]; then
    cmd+=(--glove-sn "$sn")
  fi
  if [[ "$DEBUG_LATENCY" == "1" ]]; then
    cmd+=(--debug-latency)
  fi
  echo "RDK keypoint sender: $side -> $HOST_RETARGET_HOST:$port"
  "${cmd[@]}" &
  pids+=("$!")
}

start_sender left "$LEFT_PORT" "$LEFT_GLOVE_NAME" "$LEFT_GLOVE_SN"
start_sender right "$RIGHT_PORT" "$RIGHT_GLOVE_NAME" "$RIGHT_GLOVE_SN"

wait
