#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"
if [[ "${ENABLE_HAND2:-0}" != "1" ]]; then
  echo "Refusing to energize both Hand 2 devices. Re-run with ENABLE_HAND2=1." >&2
  exit 2
fi

# Robot side: one Hand 2 server per side. Empty SN/address uses side-aware discovery.
LEFT_PORT="${LEFT_PORT:-8765}"
RIGHT_PORT="${RIGHT_PORT:-8767}"
LEFT_HAND_SN="${LEFT_HAND_SN:-}"
RIGHT_HAND_SN="${RIGHT_HAND_SN:-}"
LEFT_HAND_ADDRESS="${LEFT_HAND_ADDRESS:-}"
RIGHT_HAND_ADDRESS="${RIGHT_HAND_ADDRESS:-}"
CONTROL_RATE="${CONTROL_RATE:-60}"
SMOOTH_TAU="${SMOOTH_TAU:-0.05}"
KP="${KP:-3.0}"
KD="${KD:-0.1}"
CURRENT_LIMIT="${CURRENT_LIMIT:-1.5}"
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
  local sn="$3"
  local address="$4"
  local cmd=(
    conda run -n "$WUJI_CONDA_ENV" python "$SCRIPT_DIR/hand_qpos_server.py"
    --bind-host 0.0.0.0
    --port "$port"
    --hand "$side"
    --hand-device-name "wuji_hand_2_$side"
    --enable-hand
    --keep-listening
    --control-rate "$CONTROL_RATE"
    --smooth-tau "$SMOOTH_TAU"
    --kp "$KP"
    --kd "$KD"
    --current-limit "$CURRENT_LIMIT"
    --no-home-on-shutdown
  )
  if [[ -n "$sn" ]]; then
    cmd+=(--hand-sn "$sn")
  fi
  if [[ -n "$address" ]]; then
    cmd+=(--hand-address "$address")
  fi
  if [[ "$DEBUG_LATENCY" == "1" ]]; then
    cmd+=(--debug-latency --print-every "$PRINT_EVERY")
  fi
  echo "Robot Hand 2 server: $side port=$port sn=${sn:-auto} address=${address:-auto}"
  "${cmd[@]}" &
  pids+=("$!")
}

start_server left "$LEFT_PORT" "$LEFT_HAND_SN" "$LEFT_HAND_ADDRESS"
start_server right "$RIGHT_PORT" "$RIGHT_HAND_SN" "$RIGHT_HAND_ADDRESS"
wait
