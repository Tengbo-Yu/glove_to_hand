# Commit Notes

This file is a long-lived pre-commit log. Before each git commit, add or update
the entry for the commit being prepared. Keep entries in reverse chronological
order, and write all entries in English.

## Entry Template

```markdown
## YYYY-MM-DD - Short Commit Title

Suggested commit message:
`type: short imperative summary`

Purpose:
- Why this change is being made.
- What workflow, bug, or user need it supports.

Changed files:
- `path/to/file`: what changed and why.

Runtime or data-flow notes:
- Any important command, topic, schema, port, or behavior change.

Verification:
- Exact commands that were run.
- Result of each command.

Known limitations:
- Missing dependencies, skipped tests, follow-up work, or intentional scope boundaries.
```

## 2026-06-10 - Wuji hand MCAP replay client

Suggested commit message:
`feat: add Wuji hand MCAP replay client`

Purpose:
- Replay collected Wuji hand command telemetry without requiring live gloves or
  retargeting.
- Use the recorded hand-server command stream as the replay source so the
  reproduced command is what the hand server actually received during
  collection.
- Keep replay separate from the G1 policy replay/export path.

Changed files:
- `wuji_mcap_replay_client.py`: added a standalone MCAP replay client that
  reads `/wuji/hand/left/command` and `/wuji/hand/right/command`, extracts
  `received_qpos_5x4`, schedules a shared left/right timeline, and sends
  existing `glove-qpos-v1` TCP frames to `hand_qpos_server.py`.
- `wuji_mcap_replay_client.py`: added `--timing log_time|source_time|apply_time`,
  `--source hand_command|glove_command`, `--speed`, `--start-sec`,
  `--duration-sec`, `--max-gap-sec`, and `--dry-run`.
- `requirements.txt`: added `mcap==1.3.1` for replaying DataCollector MCAP
  episodes from the `wuji` environment.
- `.gitignore`: added `__pycache__/` so local Python bytecode cache
  directories are not picked up by git status or accidentally committed.
- `tests/test_wuji_mcap_replay_client.py`: added coverage for synthetic MCAP
  hand-command extraction, `apply_time` timestamp selection, invalid qpos shape
  rejection, timeline filtering, speed/max-gap sleep calculation, and TCP
  `glove-qpos-v1` message output.
- `README.md`: documented the dry-run-first replay workflow and short
  `--duration-sec 5` hardware smoke flow.
- `COMMIT_NOTES.md`: recorded this replay client entry.

Runtime or data-flow notes:
- Default replay source is `hand_command`, not `glove_command`.
- `hand_command` reads `received_qpos_5x4` from:
  `/wuji/hand/left/command` and `/wuji/hand/right/command`.
- `glove_command` fallback reads `retargeted_qpos_5x4` from:
  `/wuji/glove/left/command` and `/wuji/glove/right/command`.
- Default timing is MCAP `message.log_time`, which gives a shared left/right
  collector timeline. `source_time` uses MCAP `publish_time`, and `apply_time`
  uses payload `apply_timestamp_ns`.
- Default TCP targets are left `127.0.0.1:8765` and right `127.0.0.1:8766`.
- Client `--dry-run` opens no sockets. Real motion still requires starting
  `hand_qpos_server.py` with `--enable-hand`; server dry-run is the first
  replay validation step.

Verification:
- RED check before implementation:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/delta/workspace/glove_to_hand conda run -n data_collector python -B -m unittest discover -s /home/delta/workspace/glove_to_hand/tests -p 'test_wuji_mcap_replay_client.py' -v`
  failed because `wuji_mcap_replay_client` did not exist.
- Final `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/delta/workspace/glove_to_hand conda run -n data_collector python -B -m unittest discover -s /home/delta/workspace/glove_to_hand/tests -p 'test_wuji_mcap_replay_client.py' -v`
  passed with 6 tests.
- Final `git diff --check` passed with no whitespace errors.

Known limitations:
- Hardware replay was not run during implementation.
- The `wuji` environment must run `conda run -n wuji pip install -r requirements.txt`
  before using the replay client because `mcap`, `msgpack`, and `pyzmq` are
  runtime dependencies.
- This replay path sends hand joint targets only. It does not replay G1 body
  policy state, LeRobot rows, or `/wuji/hand/*/state` as control input.

## 2026-06-08 - DataCollector telemetry publisher for Wuji glove and hand

Suggested commit message:
`feat: publish Wuji telemetry to DataCollector`

Purpose:
- Publish Wuji glove retarget commands, Wuji hand received/applied commands,
  and Wuji hand state into DataCollector without blocking the live control path.
- Keep left/right hands and glove-versus-hand command boundaries separate so
  latency, dropped TCP frames, and actual hand state can be audited after MCAP
  recording.
- Preserve the Wuji retargeter native qpos layout as `5 x 4` plus `flat20`
  instead of compressing it into an ambiguous lower-dimensional vector.

Changed files:
- `data_collector_telemetry.py`: added a reusable non-blocking ZMQ `PUSH`
  publisher for DataCollector multipart messages. It sends
  `[header_json, msgpack_payload]`, uses `zmq.NOBLOCK`, tracks
  `dropped_count` on `zmq.Again`, defines default Wuji stream names/ports, and
  provides payload helpers for fixed `5 x 4` qpos fields.
- `glove_qpos_client.py`: added `--telemetry`, `--telemetry-host`, and
  `--telemetry-endpoint`. The client now publishes `glove_command` immediately
  after `Retargeter.retarget(...).reshape(5, 4)` and before sending the TCP
  qpos frame to the hand server.
- `hand_qpos_server.py`: added `--hand`, command/state telemetry toggles, and
  explicit command/state endpoints. The server now publishes `hand_command`
  after receiving the latest TCP frame and records whether the command was
  applied to a controller. It also publishes `hand_state` with target qpos,
  actual qpos, and actual effort when the Wuji controller exposes them.
- `hand_qpos_server.py`: changed realtime controller creation to use
  `enable_upstream=True` only when hand-state telemetry is enabled. If state
  telemetry is disabled, the previous `enable_upstream=False` behavior remains.
- `teleop_client.sh` and `teleop_dual_client.sh`: pass
  `DATA_COLLECTOR_HOST` through to glove telemetry clients while keeping the
  existing hand server host/port behavior.
- `teleop_server.sh` and `teleop_dual_server.sh`: pass
  `DATA_COLLECTOR_HOST` through to hand telemetry servers. The dual server now
  launches the left process with `--hand left` and the right process with
  `--hand right`, so default telemetry endpoints map to the correct ports.
- `requirements.txt`: added `msgpack` and `pyzmq`, which are required by the
  DataCollector telemetry publisher in the `wuji` conda environment.
- `tests/test_data_collector_telemetry.py`: added unit coverage for multipart
  header/payload construction, non-blocking drop counting, fixed `5 x 4` and
  `flat20` qpos payload fields, and shape validation.
- `tests/test_hand_qpos_server.py`: added unit coverage for `hand_command`
  payload fields, `hand_state` position/effort payloads, and the fallback path
  when `get_joint_actual_effort()` is unsupported.
- `COMMIT_NOTES.md`: created this long-lived commit notes file for the
  `glove_to_hand` repository.

Runtime or data-flow notes:
- Default DataCollector endpoints are:
  `glove_command/left -> tcp://<DATA_COLLECTOR_HOST>:6011`,
  `glove_command/right -> tcp://<DATA_COLLECTOR_HOST>:6012`,
  `hand_command/left -> tcp://<DATA_COLLECTOR_HOST>:6013`,
  `hand_command/right -> tcp://<DATA_COLLECTOR_HOST>:6014`,
  `hand_state/left -> tcp://<DATA_COLLECTOR_HOST>:6015`, and
  `hand_state/right -> tcp://<DATA_COLLECTOR_HOST>:6016`.
- `glove_command` payloads include `hand_side`, `seq`,
  `retargeted_qpos_5x4`, `retargeted_qpos_flat20`, `finger_names`,
  `joint_names`, `fingers_pose`, `fingers_pose_shape`, `retarget_config`,
  `glove_device_name`, `glove_sn`, `tcp_target`, `protocol`, and
  `telemetry_dropped_count`.
- `hand_command` payloads include `hand_side`, `glove_seq`,
  `glove_timestamp`, `received_qpos_5x4`, `received_qpos_flat20`, `tcp_peer`,
  `dropped_socket`, `dropped_socket_total`, `enable_hand`,
  `applied_to_controller`, `apply_timestamp_ns`, and
  `telemetry_dropped_count`.
- `hand_state` payloads include `hand_side`, `target_qpos_5x4`,
  `target_qpos_flat20`, `actual_qpos_5x4`, `actual_qpos_flat20`,
  `actual_effort_5x4`, `actual_effort_flat20`, `state_available`,
  `effort_supported`, `read_error`, `hand_serial`, `lowpass`,
  `enable_upstream`, and `telemetry_dropped_count`.
- `finger_names` is fixed as `thumb,index,middle,ring,pinky`.
- `joint_names` is fixed as `MCP_aa,MCP_fe,PIP,DIP`.
- The ZMQ sender is intentionally best-effort: DataCollector not running or a
  full send queue must not stop glove retargeting or hand control.
- Dry-run hand server mode can still publish command telemetry. State telemetry
  in dry-run records the target qpos and reports `state_available=false` with
  `read_error=controller_not_available`.

Verification:
- RED checks were run before implementation:
  `PYTHONPATH=data_collector/src python -m unittest discover -s data_collector/tests -p 'test_delta_data_config.py' -v`
  failed because the six Wuji streams were missing.
- RED checks were run before implementation:
  `PYTHONPATH=/home/delta/workspace/glove_to_hand python -m unittest discover -s /home/delta/workspace/glove_to_hand/tests -p 'test_data_collector_telemetry.py' -v`
  failed because `data_collector_telemetry` did not exist.
- RED checks were run before implementation:
  `PYTHONPATH=/home/delta/workspace/glove_to_hand conda run -n wuji python -m unittest discover -s /home/delta/workspace/glove_to_hand/tests -p 'test_hand_qpos_server.py' -v`
  failed because `build_hand_state_payload` and `publish_hand_command` did not
  exist.
- Final `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/delta/workspace/glove_to_hand conda run -n wuji python -B -m unittest discover -s /home/delta/workspace/glove_to_hand/tests -v`
  passed with 8 tests.
- Final `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=data_collector/src python -B -m unittest discover -s data_collector/tests -p 'test_delta_data_config.py' -v`
  passed with 2 tests in the paired DataCollector repository.
- Final real local ZMQ/msgpack smoke check passed by binding a temporary
  `zmq.PULL` socket, publishing through `DataCollectorTelemetryPublisher`, and
  unpacking the received multipart header/payload.
- Final `git -C /home/delta/workspace/glove_to_hand diff --check` passed with
  no whitespace errors.

Known limitations:
- Hardware Wuji glove/hand motion was not run during this implementation.
- The `wuji` environment did not have `pyzmq` or `msgpack` before this change.
  Run `conda run -n wuji pip install -r requirements.txt` before live use.
- `lerobot_export.py` is not updated in this repository. Training/export schema
  merging should be implemented after collecting and inspecting real Wuji MCAP
  samples.
- DataCollector stream definitions and collection docs live in the separate
  `/home/delta/workspace/DataCollector` repository and have their own
  `COMMIT_NOTES.md` entry.
