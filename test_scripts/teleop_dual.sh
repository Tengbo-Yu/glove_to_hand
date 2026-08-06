#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"
ENABLE_HAND2="${ENABLE_HAND2:-0}"

CMD=(
  conda run -n "$WUJI_CONDA_ENV" python "$PROJECT_ROOT/glove_to_hand_dual.py"
  --left-glove-name "${LEFT_GLOVE_NAME:-glove_l}"
  --right-glove-name "${RIGHT_GLOVE_NAME:-glove_r}"
  --left-glove-sn "${LEFT_GLOVE_SN:-}"
  --right-glove-sn "${RIGHT_GLOVE_SN:-}"
  --left-hand-sn "${LEFT_HAND_SN:-}"
  --right-hand-sn "${RIGHT_HAND_SN:-}"
  --rate "${RATE:-60}"
  --current-limit "${CURRENT_LIMIT:-1.0}"
  --no-home-on-shutdown
)
if [[ "$ENABLE_HAND2" == "1" ]]; then
  CMD+=(--enable-hand)
else
  echo "Dual dry run only. Set ENABLE_HAND2=1 to enable both Hand 2 devices."
fi
"${CMD[@]}"
