#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Fill these if multiple hands are connected. Leave empty to let wujihandpy auto-select.
LEFT_HAND_SERIAL=""
RIGHT_HAND_SERIAL=""

# One server per hand, each on its own port. Clients must use the matching ports.
LEFT_PORT="8765"
RIGHT_PORT="8766"

pids=()
cleanup() {
  trap - INT TERM
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

start_server() {
  local port="$1"
  local serial="$2"
  local cmd=(
    conda run -n wuji python "$SCRIPT_DIR/hand_qpos_server.py"
    --bind-host 0.0.0.0
    --port "$port"
    --enable-hand
    --rate 60
    --lowpass 10
    --home-duration 2
  )
  if [[ -n "$serial" ]]; then
    cmd+=(--hand-serial "$serial")
  fi
  "${cmd[@]}" &
  pids+=("$!")
}

start_server "$LEFT_PORT" "$LEFT_HAND_SERIAL"
start_server "$RIGHT_PORT" "$RIGHT_HAND_SERIAL"

wait
