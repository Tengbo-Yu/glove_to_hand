#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Host side: receive both hands' keypoints from RDK, retarget, and forward qpos to robot.
ROBOT_HAND_HOST="${ROBOT_HAND_HOST:-127.0.0.1}"
LEFT_PORT="${LEFT_PORT:-8765}"
RIGHT_PORT="${RIGHT_PORT:-8766}"
PRINT_EVERY="${PRINT_EVERY:-0.5}"
RETARGET_LP_ALPHA="${RETARGET_LP_ALPHA:-0.6}"
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

start_bridge() {
  local side="$1"
  local port="$2"
  local cmd=(
    python "$SCRIPT_DIR/host_retarget_bridge.py"
    --bind-host 0.0.0.0
    --listen-port "$port"
    --robot-host "$ROBOT_HAND_HOST"
    --robot-port "$port"
    --hand "$side"
    --print-every "$PRINT_EVERY"
    --retarget-lp-alpha "$RETARGET_LP_ALPHA"
  )
  if [[ "$DEBUG_LATENCY" == "1" ]]; then
    cmd+=(--debug-latency)
  fi
  echo "Host retarget bridge: RDK:$port -> robot $ROBOT_HAND_HOST:$port ($side)"
  "${cmd[@]}" &
  pids+=("$!")
}

start_bridge left "$LEFT_PORT"
start_bridge right "$RIGHT_PORT"

wait
