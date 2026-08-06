#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"

# IP address(es) of the machine(s) running the hand servers.
# When the hands are on the same machine, both hosts are the same IP.
# Use 127.0.0.1 only when the dual clients and dual servers run on this same machine.
LEFT_HAND_SERVER_HOST="10.1.10.166"
RIGHT_HAND_SERVER_HOST="10.1.10.166"
LEFT_PORT="8765"
RIGHT_PORT="8766"

# wuji-sdk device names for each glove.
LEFT_GLOVE_NAME="glove_l"
RIGHT_GLOVE_NAME="glove_r"

# Fill these if multiple Wuji gloves are online. Leave empty to auto-connect.
RIGHT_GLOVE_SN="WG1KA03260512012"
LEFT_GLOVE_SN="WG1JA03260517019"

# Set DEBUG_LATENCY=1 to print stage timing from both client processes.
DEBUG_LATENCY="${DEBUG_LATENCY:-0}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
GLOVE_STREAM="${GLOVE_STREAM:-offline_hand_skeleton}"
WUJI_LOG_LEVEL="${WUJI_LOG_LEVEL:-error}"

pids=()
cleanup() {
  trap - INT TERM
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

start_client() {
  local host="$1"
  local port="$2"
  local side="$3"
  local name="$4"
  local sn="$5"
  local cmd=(
    conda run -n "$WUJI_CONDA_ENV" python "$PROJECT_ROOT/glove_qpos_client.py"
    --host "$host"
    --port "$port"
    --hand "$side"
    --device-name "$name"
    --rate 60
    --print-every "$PRINT_EVERY"
    --glove-stream "$GLOVE_STREAM"
    --wuji-log-level "$WUJI_LOG_LEVEL"
  )
  if [[ -n "$sn" ]]; then
    cmd+=(--glove-sn "$sn")
  fi
  if [[ "$DEBUG_LATENCY" == "1" ]]; then
    cmd+=(--debug-latency)
  fi
  "${cmd[@]}" &
  pids+=("$!")
}

start_client "$LEFT_HAND_SERVER_HOST" "$LEFT_PORT" "left" "$LEFT_GLOVE_NAME" "$LEFT_GLOVE_SN"
start_client "$RIGHT_HAND_SERVER_HOST" "$RIGHT_PORT" "right" "$RIGHT_GLOVE_NAME" "$RIGHT_GLOVE_SN"

wait
