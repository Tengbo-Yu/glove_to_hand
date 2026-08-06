#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${ENABLE_HAND2:-0}" != "1" ]]; then
  echo "Refusing to energize both Hand 2 devices. Re-run with ENABLE_HAND2=1." >&2
  exit 2
fi
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}" \
exec "$PROJECT_ROOT/teleop_dual_robot_hand.sh"
