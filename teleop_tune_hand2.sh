#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WUJI_CONDA_ENV="${WUJI_CONDA_ENV:-wuji_new}"
HAND_SIDE="${HAND_SIDE:-right}"
LEFT_GLOVE_NAME="${LEFT_GLOVE_NAME:-glove_l}"
RIGHT_GLOVE_NAME="${RIGHT_GLOVE_NAME:-glove_r}"
LEFT_GLOVE_SN="${LEFT_GLOVE_SN:-WG1JA03260517019}"
RIGHT_GLOVE_SN="${RIGHT_GLOVE_SN:-WG1KA03260512012}"

case "$HAND_SIDE" in
  left)
    GLOVE_NAME="$LEFT_GLOVE_NAME"
    GLOVE_SN="$LEFT_GLOVE_SN"
    DEFAULT_CONFIG="$SCRIPT_DIR/wuji-retargeting/example/config/adaptive_analytical_wuji_glove_wuji_hand_2_left.yaml"
    ;;
  right)
    GLOVE_NAME="$RIGHT_GLOVE_NAME"
    GLOVE_SN="$RIGHT_GLOVE_SN"
    DEFAULT_CONFIG="$SCRIPT_DIR/config/hand2_right_teleop.yaml"
    ;;
  *)
    echo "ERROR: HAND_SIDE must be 'left' or 'right', got '$HAND_SIDE'." >&2
    exit 1
    ;;
esac

RETARGET_CONFIG="${RETARGET_CONFIG:-$DEFAULT_CONFIG}"

echo "Hand 2 tuning viewer: $HAND_SIDE glove=$GLOVE_SN"
echo "Live-edit: $RETARGET_CONFIG"
exec conda run --no-capture-output -n "$WUJI_CONDA_ENV" \
  python -u "$SCRIPT_DIR/wuji-retargeting/example/tuning_tool.py" \
  --wuji-glove \
  --hand "$HAND_SIDE" \
  --device-name "$GLOVE_NAME" \
  --glove-sn "$GLOVE_SN" \
  --config "$RETARGET_CONFIG"
