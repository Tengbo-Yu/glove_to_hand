# Commit Notes

This file is the pre-commit change log for this repository. Keep entries in
reverse chronological order and record scope, runtime effects, validation, and
known limitations before each commit.

## 2026-08-10 - Adapt Hand 2 telemetry to Hammerhead DataCollector

Base and branch:
- Base: `RDK_X5_hand2` at `a5bdf9e`.
- New branch: `data_collect_hand2`.

Suggested commit message:
`feat: adapt Hand 2 telemetry to Hammerhead DataCollector`

Purpose:
- Carry the existing `data_collect` branch's six Wuji DataCollector streams
  forward without replacing the current Hand 2 SDK, robot-side retargeting,
  Wi-Fi keypoint transport, smoothing, enable gate, or command watchdog.
- Preserve the source boundaries needed to audit raw glove input, retargeted
  hand targets, commands applied after output smoothing, and measured Hand 2
  joint positions.
- Keep the telemetry sidecar best-effort and non-blocking so collector absence
  or queue saturation cannot stall the live control loop.

Changed files:
- `data_collector_telemetry.py`: adds the Hammerhead-compatible two-part ZMQ
  publisher, the `6011` through `6016` endpoint map, Hand 2 qpos/keypoint
  validators, explicit schemas, clock-domain metadata, and non-blocking drop
  accounting.
- `glove_qpos_client.py`: publishes each RDK glove command before TCP delivery,
  including the raw `21 x 3` keypoints, cache/freshness marker, device metadata,
  optional RDK-retargeted qpos, and the `glove-qpos-v2` target.
- `hand_qpos_server.py`: publishes server-retargeted targets and smoothed
  applied qpos on the hand-command stream, preserves legacy
  `received_qpos_5x4` for replay compatibility, and publishes latest Hand 2
  feedback with freshness and actual feedback timestamps.
- `teleop_rdk_keypoints.sh`, `teleop_dual_rdk_keypoints.sh`: route glove
  telemetry over the Wi-Fi kit to `DATA_COLLECTOR_HOST`, defaulting to the
  current `HOST_RETARGET_HOST`, with `DATA_COLLECTOR_TELEMETRY=0` as an opt-out.
- `wuji_mcap_replay_client.py`: ports dry-run-first MCAP replay to
  `glove-qpos-v2`, verified Hand 2 device order, and current left/right server
  ports `8765`/`8767`.
- `requirements.txt`: adds `msgpack`, `pyzmq`, and `mcap` runtime dependencies.
- `README_RDK.md`: documents ports/topics, clock boundaries, startup acceptance,
  MCAP evidence requirements, telemetry opt-out, and safe replay gates.
- `tests/test_data_collector_telemetry.py`: covers endpoints, multipart framing,
  non-blocking drops, raw keypoints, and Hand 2 qpos validation.
- `tests/test_hand_qpos_server.py`: covers new Hand 2 command/state payloads and
  retained replay fields.
- `tests/test_wuji_mcap_replay_client.py`: covers synthetic Hammerhead MCAP
  extraction, replay scheduling, v2 framing/device order, and port defaults.

Runtime and data-flow notes:
- Hammerhead endpoints remain:
  `glove left/right -> 6011/6012`, `hand command left/right -> 6013/6014`, and
  `hand state left/right -> 6015/6016`.
- The collector contract is `[header_json, msgpack_payload]`; each header names
  the configured Hammerhead source, `encoding=msgpack`, sequence, frame ID, and
  source timestamp.
- RDK glove timestamps and hand-server timestamps are labeled as different
  clock domains. The code does not claim cross-machine one-way latency without
  externally verified clock synchronization.
- No DataCollector repository files, datasets, services, robot processes, or
  hardware were modified or started.

Verification:
- `git diff --check`: passed.
- `bash -n teleop_rdk_keypoints.sh teleop_dual_rdk_keypoints.sh
  teleop_direct_hand.sh teleop_robot_hand.sh teleop_dual_robot_hand.sh`: passed.
- Python compilation of the four changed/added entrypoints with the current
  DataCollector Python 3.10 interpreter: passed.
- Focused unittest run for telemetry, server protocol/payloads, and replay:
  `22 tests`, passed.
- Current DataCollector `hammerhead` config test
  `data_collector/tests/test_delta_hammerhead_config.py`: `3 passed`.
- Programmatic comparison against current
  `data_collector/configs/delta_hammerhead.yaml`: all six stream names, topics,
  ports, and `msgpack` encodings matched.
- Real local ZMQ smoke test: publisher multipart was received, JSON-decoded, and
  msgpack-decoded successfully (`real_zmq_multipart_ok`).

Known limitations:
- Live Wuji glove, Hand 2 motion, Wi-Fi traffic, and a recorded Hammerhead MCAP
  were not exercised; all validation was simulator-/socket-only and did not
  enable hardware.
- The complete pre-existing test suite could not run in one installed
  environment: the DataCollector venv lacks `nlopt`, while the available `byd`
  `nlopt` binary is built against NumPy 1.x and is incompatible with the
  required NumPy 2.2.6. The four affected pre-existing retarget tests fail at
  import; all remaining/new focused tests pass.
- Hand 2 state exposes joint positions through the current backend but not
  effort feedback, so state payloads explicitly set `effort_supported=false`.
- `glove_command` replay is only available for recordings made with client-side
  qpos mode; the current recommended keypoint mode should replay from
  `hand_command`, which always contains `received_qpos_5x4`.
