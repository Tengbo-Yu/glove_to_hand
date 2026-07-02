#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# IP address(es) of the machine(s) running the hand servers.
# When the hands are on the same machine, both hosts are the same IP.
# Use 127.0.0.1 only when the dual clients and dual servers run on this same machine.
LEFT_HAND_SERVER_HOST="192.168.123.164"
RIGHT_HAND_SERVER_HOST="192.168.123.164"
LEFT_PORT="8765"
RIGHT_PORT="8766"

# wuji-sdk device names for each glove.
LEFT_GLOVE_NAME="glove_l"
RIGHT_GLOVE_NAME="glove_r"

# Fill these if multiple Wuji gloves are online. Leave empty to auto-connect.

# Set DEBUG_LATENCY=1 to print stage timing from both client processes.
DEBUG_LATENCY="${DEBUG_LATENCY:-0}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"

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
    conda run -n wuji python "$SCRIPT_DIR/glove_qpos_client.py"
    --host "$host"
    --port "$port"
    --hand "$side"
    --device-name "$name"
    --rate 60
    --print-every "$PRINT_EVERY"
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
