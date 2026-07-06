#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Robot side: receive retargeted qpos for both hands and drive both Wuji Hands.
LEFT_PORT="${LEFT_PORT:-8765}"
RIGHT_PORT="${RIGHT_PORT:-8767}"
LEFT_HAND_SERIAL="${LEFT_HAND_SERIAL:-3378387C3233}"
RIGHT_HAND_SERIAL="${RIGHT_HAND_SERIAL:-337338793233}"
CONTROL_RATE="${CONTROL_RATE:-60}"
LOWPASS="${LOWPASS:-15}"
SMOOTH_TAU="${SMOOTH_TAU:-0.05}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
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

start_server() {
  local side="$1"
  local port="$2"
  local serial="$3"
  local cmd=(
    python "$SCRIPT_DIR/hand_qpos_server.py"
    --bind-host 0.0.0.0
    --port "$port"
    --hand "$side"
    --enable-hand
    --keep-listening
    --rate 60
    --control-rate "$CONTROL_RATE"
    --lowpass "$LOWPASS"
    --smooth-tau "$SMOOTH_TAU"
    --home-duration 2
  )
  if [[ -n "$serial" ]]; then
    cmd+=(--hand-serial "$serial")
  fi
  if [[ "$DEBUG_LATENCY" == "1" ]]; then
    cmd+=(--debug-latency --print-every "$PRINT_EVERY")
  fi
  echo "Robot hand qpos server: $side on port $port serial=$serial"
  "${cmd[@]}" &
  pids+=("$!")
}

start_server left "$LEFT_PORT" "$LEFT_HAND_SERIAL"
start_server right "$RIGHT_PORT" "$RIGHT_HAND_SERIAL"

wait
