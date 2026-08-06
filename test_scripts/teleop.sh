#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"
HAND_SIDE="${HAND_SIDE:-right}"
ENABLE_HAND2="${ENABLE_HAND2:-0}"

CMD=(
  conda run -n "$WUJI_CONDA_ENV" python "$PROJECT_ROOT/glove_to_hand.py"
  --hand "$HAND_SIDE"
  --duration "${DURATION:-100}"
  --rate "${RATE:-60}"
  --current-limit "${CURRENT_LIMIT:-1.5}"
  --no-home-on-shutdown
)
if [[ "$ENABLE_HAND2" == "1" ]]; then
  CMD+=(--enable-hand)
else
  echo "Dry run only. Set ENABLE_HAND2=1 to explicitly enable Wuji Hand 2."
fi
"${CMD[@]}"
