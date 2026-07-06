#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Fill these if multiple hands are connected. Leave empty to let wujihandpy auto-select.
LEFT_HAND_SERIAL="3378387C3233"
RIGHT_HAND_SERIAL="337338793233"

# One server per hand, each on its own port. Clients must use the matching ports.
LEFT_PORT="8765"
RIGHT_PORT="8766"

CONTROL_RATE="${CONTROL_RATE:-60}"
LOWPASS="${LOWPASS:-15}"
SMOOTH_TAU="${SMOOTH_TAU:-0.05}"
RETARGET_LP_ALPHA="${RETARGET_LP_ALPHA:-0.6}"

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
  local side="$3"
  local cmd=(
    python "$SCRIPT_DIR/hand_qpos_server.py"
    --bind-host 0.0.0.0
    --port "$port"
    --hand "$side"
    --enable-hand
    --rate 60
    --control-rate "$CONTROL_RATE"
    --lowpass "$LOWPASS"
    --smooth-tau "$SMOOTH_TAU"
    --retarget-lp-alpha "$RETARGET_LP_ALPHA"
    --home-duration 2
  )
  if [[ -n "$serial" ]]; then
    cmd+=(--hand-serial "$serial")
  fi
  if [[ "${DEBUG_LATENCY:-0}" == "1" ]]; then
    cmd+=(--debug-latency --print-every "${PRINT_EVERY:-0.5}")
  fi
  "${cmd[@]}" &
  pids+=("$!")
}

start_server "$LEFT_PORT" "$LEFT_HAND_SERIAL" "left"
start_server "$RIGHT_PORT" "$RIGHT_HAND_SERIAL" "right"

wait
