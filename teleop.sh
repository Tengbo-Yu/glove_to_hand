#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# conda run -n wuji python "$SCRIPT_DIR/glove_to_hand.py" \
# --enable-hand \
# --duration 100 \
# --rate 60 \
# --lowpass 10 \
# --gain -1.5 \
# --max-delta 1.2 \
# --joint-gains "-1.5,-1.5,-1.5,-1.5, -1.5,-0.7,-1.5,-1.5, -1.5,-0.7,-1.5,-1.5, -1.5,-0.1,-1.5,-1.5, -1.5,-0.1,-1.5,-1.5" \
# --joint-max-deltas "1.2,1.2,1.2,1.2, 1.2,1.0,1.2,1.2, 1.2,1.0,1.2,1.2, 1.2,0.1,1.2,1.2, 1.2,0.1,1.2,1.2" \
# --invert-side-sway \
# --max-velocity 3.0 \
# --confidence-threshold 0.3 \
# --home-duration 5 \
# --diagnostics


conda run -n wuji python "$SCRIPT_DIR/glove_to_hand.py" \
--enable-hand \
--duration 100 \
--hand left \
--home-duration 2 \
--diagnostics \
--rate 60 \
--lowpass 10 
