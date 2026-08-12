#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Field-validated direct path:
# RDK X5 -> Unitree wlan0 -> robot-side retargeting -> dual Hand2 services.
# Keep telemetry off until the Hammerhead collector is separately accepted.
UNITREE_WIFI_HOST="${UNITREE_WIFI_HOST:-192.168.112.106}"
LEFT_PORT="${LEFT_PORT:-8765}"
RIGHT_PORT="${RIGHT_PORT:-8767}"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"
DATA_COLLECTOR_TELEMETRY="${DATA_COLLECTOR_TELEMETRY:-0}"
DRY_RUN="${DRY_RUN:-0}"

export HOST_RETARGET_HOST="$UNITREE_WIFI_HOST"
export LEFT_PORT RIGHT_PORT WUJI_CONDA_ENV DATA_COLLECTOR_TELEMETRY

echo "Direct Unitree Wi-Fi teleop: ${HOST_RETARGET_HOST}:${LEFT_PORT}/${RIGHT_PORT}"
echo "Conda environment: ${WUJI_CONDA_ENV}"
echo "DataCollector telemetry: ${DATA_COLLECTOR_TELEMETRY}"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry run only; sender was not started."
  exit 0
fi

exec "$SCRIPT_DIR/teleop_dual_rdk_keypoints.sh"
