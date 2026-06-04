# conda run -n wuji python /home/user/workspace/wuji/glove_to_hand.py \
# --enable-hand \
# --duration 10 \
# --rate 100 \
# --lowpass 30 \
# --gain -1.0 \
# --max-delta 1.2 \
# --confidence-threshold 1.5 \
# --diagnostics \
# --home-duration 5


conda run -n wuji python /home/user/workspace/wuji/glove_to_hand.py \
--enable-hand \
--duration 100 \
--rate 60 \
--lowpass 10 \
--gain -1.5 \
--max-delta 1.2 \
--joint-gains "-1.5,-1.5,-1.5,-1.5, -1.5,-0.7,-1.5,-1.5, -1.5,-0.7,-1.5,-1.5, -1.5,-0.1,-1.5,-1.5, -1.5,-0.1,-1.5,-1.5" \
--joint-max-deltas "1.2,1.2,1.2,1.2, 1.2,1.0,1.2,1.2, 1.2,1.0,1.2,1.2, 1.2,0.1,1.2,1.2, 1.2,0.1,1.2,1.2" \
--invert-side-sway \
--max-velocity 3.0 \
--confidence-threshold 0.3 \
--home-duration 5 \
--diagnostics
