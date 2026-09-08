# Commit Notes

This file is the pre-commit change log for this repository. Keep entries in
reverse chronological order and record scope, runtime effects, validation, and
known limitations before each commit.

## 2026-09-08 - Record completed PC-initiated telemetry switch

- Base:7a5ba0d; intended subject: docs(hand2): record verified telemetry switch.
- Documentation only:239 tunnel activated; onboard env and matching guard entry
  updated. Both original active idle hand services restarted without motion.
- Validation:4 real end-to-end ZMTP handshakes passed without data injection;
  runtime image/SN/kp/kd unchanged; collector PID unchanged; tunnel uses wired
 192.168.123.138 ->192.168.123.164. Backup/rollback artifacts are retained.
- No other runtime changes in this documentation commit; hardware acceptance pending.

## 2026-09-08 - Make robot telemetry destination PC-independent

- Base: d8d3ebf9728429cb4b2658b539206c0f71fdc8eb.
- Scope: unitree_hand2 field and example env defaults plus transport/deployment
  documentation. Change only telemetry host to127.0.0.1; reuse the PC's existing
  authenticated reverse SSH tunnel. No image/SDK/control/hand SN/gain changes.
- Preserve existing wire format and nonblocking PUSH behavior. Only hand
  command/state6013–6016 are covered; separate glove publishers are not changed.
- Validation:6 hardware-free telemetry tests passed; real isolated26014 tunnel
  test passed with byte-identical multipart payload and timestamp, no production
  data injection and no hand command. Git diff check passed before commit.
- Deployment is prepared, not activated: sudo credentials on both machines are
  required. Exact current env and service source have been backed up separately.
  Rollback restores env, its guard entry and prior service states.
- Intended subject: fix(hand2): use PC-initiated telemetry tunnel by default.

## 2026-09-07 - Sync the running 239 Unitree Hand2 implementation

- Base: `data_collect_hand2@8bd7039`; source: running left/right containers on
  Unitree `192.168.123.164`, accessed through PC `10.1.10.239` (read-only).
- Import the live `hand_qpos_server.py` byte-for-byte: independent 30 Hz
  telemetry and fresh-feedback-only state publication. Retain matching backend,
  retarget code and the more complete local regression tests.
- Copy the actual target env; update the SDK lock to `2026.8.31`, example
  profile and deployment documentation. Document the external DeltaCollect
  configuration guard without introducing a dependency in the base service.
- Add a hardware-free regression test for repeated telemetry from one target,
  independent sequences, fresh-state-only publication and faster control output.
- Validation: 29 focused tests passed. Four retarget tests require missing
  `nlopt`; MCAP replay module requires missing `mcap`. No hardware acceptance,
  image rebuild or installation was performed.
- Exact source paths/hash and deployment boundaries:
  `deploy/unitree_hand2/LIVE_SYNC_20260907.md`.
- No remote writes, restarts, git commit or push.

## 2026-08-13 - Smooth robot-side Hand2 retargeting and harden the live path

Base and branch:
- Branch/worktree: `data_collect_hand2` in
  `/home/descfly/workspace/code/glove_to_hand`, base `1f0c1e6`.
- Runtime target: Unitree Orin NX at `192.168.123.164`, RDK X5 at
  `192.168.112.230`, dual Hand2 on `192.168.1.110/.111`.

Root cause and measurements before the change:
- Delta Wi-Fi signal was strong and Hand2 writes were only about `0.2 ms`; neither
  link bandwidth nor the SDK command publisher was the primary stutter source.
- Robot-side NLopt/Pinocchio retargeting was synchronous with fixed-rate Hand2
  output. Left usually took `10-18 ms`; the moving right hand often took `50-70
  ms`, reached the 50-evaluation limit, and once reached about `99 ms`. Right
  receive/control consequently fell to about `20-35 Hz` while the RDK sent 120 Hz.
- A transient empty Hand2 joint-state feedback frame raised `RuntimeError`, exited
  the right service, and broke the matching RDK TCP sender.
- A later production run exposed a separate roughly two-second simultaneous Wi-Fi
  stall while both Delta clients had power saving enabled. The one-second command
  watchdog correctly disabled both hands, but exited both containers.

Changed files and behavior:
- `hand_qpos_server.py` moves keypoint retargeting to a latest-frame worker. It
  keeps at most one pending pose, never builds a stale FIFO, and lets the smoother
  continue fixed-rate Hand2 writes while the optimizer runs. Python's thread
  switch interval is reduced from 5 ms to 1 ms during the session.
- The server accepts `--retarget-maxeval`; the deployed and repository service
  profile uses `20` evaluations to bound complex-pose CPU time. Production status
  now reports receive, retarget and control frequencies even without latency ACKs.
- A command timeout ends and disables only the current session; the keep-listening
  process remains alive. The enable-on-first-valid-frame and one-second fail-safe
  boundaries are unchanged.
- `hand2_backend.py` ignores and counts transient invalid nonblocking feedback
  frames. The strict pre-enable measured-position read still fails closed.
- The Unitree wrapper/env example forwards `RETARGET_MAXEVAL`. Tests cover newest-
  pending semantics, invalid feedback and non-exceptional command timeout.
- `WUJI_HAND_TELEOP_SOP.md` records current frequency acceptance, CPU budget,
  persistent Wi-Fi power-save checks and the GPU boundary.

Live deployment and acceptance:
- Built immutable Unitree ARM64 image
  `codex/glove-to-hand-hand2:smooth-v2-20260813`, image ID
  `sha256:484d9c38eb2a...`, and installed it in `/etc/default/wuji-hand2` with
  `RETARGET_MAXEVAL=20`, `DEBUG_LATENCY=0`, `CONTROL_RATE=200`, `SMOOTH_TAU=.02`
  and `MAX_JOINT_VELOCITY=6`. The prior env is backed up as
  `/etc/default/wuji-hand2.before_async_retarget_20260813`.
- Set the existing Delta NetworkManager profiles on both Unitree and RDK to
  `802-11-wireless.powersave=disable`; live `iw` checks reported power save off.
  No IP address, route, SSID or robot management configuration was changed.
- With the default 50 evaluations, async scheduling raised right control from the
  former `20-35 Hz` to roughly `130-170 Hz`. With 20 evaluations and production
  diagnostics off, sustained logs commonly showed input `45-95 Hz`, retarget
  `35-80 Hz`, and Hand2 output `170-200 Hz`.
- A final 90-second dual-hand hardware run survived active right-hand motion with
  both Unitree services and the RDK sender active, both Unitree `NRestarts=0`, no
  simultaneous zero-input stall, and both Delta links still power-save off.
- Explicit RDK stop produced two client-disconnect and two session-disabled logs;
  both Unitree services stayed active/listening with `NRestarts=0`.

Verification:
- Exact patched sources mounted into the deployed ARM64 dependency image passed
  all 26 Hand2/server tests, including real `nlopt`, Pinocchio and Hand2 models.
- Local dependency-free server/backend tests passed; local system Python still
  lacks `nlopt`, so model construction was validated only in the ARM64 image.
- `python3 -m py_compile`, shell syntax, `git diff --check`, deployed source hashes,
  image ID, service enablement/listeners and live safety shutdown were checked.

Known limitations:
- GPU acceleration was not implemented. The Orin NX GPU exists, but the current
  NLopt/Pinocchio objective is CPU/Python and the deployed image has no CUDA ML
  stack; the ZED workload already uses the GPU. A CUDA rewrite needs independent
  numerical equivalence and real-hand safety validation.
- The final sustained run is hardware evidence for smoothness/frequency and safety,
  but subjective operator feel still needs confirmation. The 20-evaluation budget
  trades some optimizer convergence for bounded latency.
- Delta Wi-Fi crosses different AP BSSIDs and still shows short rate variation.
  Power-save-off survived the live run but has not yet been reboot-accepted on both
  devices.
- DataCollector remained disabled/read-only and was not part of this acceptance.

## 2026-08-12 - Merge the latest RDK instructions and record field-verified direct control

Base and branch:
- Target branch/worktree: `data_collect_hand2` at `65343df`, checked out only in
  `/home/descfly/workspace/code/glove_to_hand_data_collect_hand2_fix`.
- Merged branch: `RDK_X5_hand2` at `c3599d3` with `--no-ff --no-commit`.
- Merged commits: `a16c373` (RDK access/README update) and `c3599d3` (current
  RDK Wi-Fi address, dual-host command correction and environment notes).
- The target branch already contains `7abfcdb`, `8f9825b`, `1242320` and
  `65343df`; this merge preserves both the Hammerhead telemetry work and the
  three Unitree deployment/network safety commits.

Suggested commit message:
`merge: record field-verified RDK to Unitree Hand2 path`

Purpose and resulting topology:
- Record the hardware-proven path as two Wuji gloves -> RDK X5
  `192.168.112.230` -> Delta Wi-Fi -> Unitree `wlan0`
  `192.168.112.106` -> robot-side retargeting on ports `8765/8767` -> left and
  right Hand2. The development PC is not in the command path.
- Preserve Unitree management on `192.168.123.164/24` and its separate Hand2
  address/routes. No Hand2 persistent IP change was required.
- Keep the generic RDK scripts configurable. Add a field-profile wrapper rather
  than changing the legacy `10.1.10.166:8865/8866` defaults globally.

Changed files:
- `README.md`: replaces the stale local-PC/three-stage overview with the current
  direct topology, RDK runtime routes, startup/stop order, watchdog boundary,
  persistence boundary, and links to the detailed RDK and Unitree documents.
  It does not store an SSH password.
- `README_RDK.md`: adds the current dual-hand direct procedure, makes the DHCP
  address and RDK runtime route limitations explicit, and separates the
  field-verified control path from DataCollector acceptance and legacy modes.
- `teleop_dual_rdk_unitree_wifi.sh`: encodes the exact field profile
  (`192.168.112.106`, ports `8765/8767`, `wuji_new`) while defaulting
  DataCollector telemetry off until the collector is independently accepted.
- `deploy/unitree_hand2/README.md` and
  `deploy/unitree_hand2/UNITREE_HAND2_SERVICE_SOP.md`: replace the former
  “motion unverified” boundary with the actual dual-hand evidence, retain the
  DataCollector limitation, and record normal stop/disable behavior.
- `COMMIT_NOTES.md`: records merge ancestry, deployed/runtime state, evidence,
  validation and remaining limitations before the merge commit.

Live target and field evidence:
- Unitree boot ID was `17463a66-2a5f-49a5-84fa-4e99ee40bfb9`. NetworkManager
  connected `wlan0` to `Delta` as `192.168.112.106/24`; the connection is set
  to autoconnect. Unitree -> RDK measured 10/10 ICMP replies, `0%` loss and
  `5.755 ms` average RTT.
- RDK used repository `RDK_X5_hand2` at `c3599d3`, conda env `wuji_new`, Wi-Fi
  `192.168.112.230/24`, and a runtime-only `192.168.1.20/24` plus exact routes
  to glove addresses `.100` and `.101`. Both gloves answered reachability
  probes before control.
- The sender used `HOST_RETARGET_HOST=192.168.112.106`, `LEFT_PORT=8765`,
  `RIGHT_PORT=8767`, and `DATA_COLLECTOR_TELEMETRY=0`. Both TCP connections were
  established directly from the RDK; no local-PC bridge ran.
- Unitree matched left `WH2JA01260717002` and right `WH2KA01260730030`, firmware
  `2.2.3`, handedness and `online=20/20`. Both received first valid frames,
  warmed retargeting, enabled at `KP=3.5`, `KD=0.1`, current limit `1.5 A`, and
  showed sustained increasing receive sequences while physically following the
  gloves. All three Unitree units stayed active and both hand units reported
  `NRestarts=0`.
- The RDK sender was stopped at 23:10:52. Both Unitree services logged client
  disconnect followed by `Session ended. Hand 2 disabled and socket closed`;
  established connections and RDK sender processes were absent afterward,
  while `8765/8767` continued listening for a future client.

Runtime effects and persistence:
- Last verified RDK sender state after acceptance was stopped/inactive. The
  field run used a transient user unit with `Restart=on-failure`; it was reset
  and removed after stopping and is not a boot service.
- At stop acceptance, Unitree Hand2 network/left/right services remained enabled
  and active, but both hands were disabled without a valid sender. Unitree
  `Delta` Wi-Fi is an autoconnect NetworkManager profile.
- RDK `192.168.1.20/24` and its glove routes were added with runtime `ip`
  commands and will disappear after RDK reboot. Unitree Wi-Fi uses DHCP, so its
  `.112.106` address must be rechecked after reboot.
- No DataCollector process, configuration or production data was modified.

Verification before commit:
- `bash -n` passed for every tracked/new `*.sh` file.
- `teleop_dual_rdk_unitree_wifi.sh` passed `DRY_RUN=1` with both its production
  defaults and complete test overrides. The output confirmed host, both ports,
  conda environment and telemetry flag; no sender or hardware connection was
  started.
- Local focused tests passed `25` cases: DataCollector telemetry `5`, Hand2
  server/protocol `13`, MCAP replay `4`, and dependency-free Hand2 adaptation
  `7`. The MCAP suite used `mcap` installed only in a temporary `/tmp` target.
- Four retarget construction tests could not import local `nlopt`; they did not
  reach assertions. This is the already-recorded system-Python native dependency
  gap. The exact deployed ARM64 image previously passed all `29` Hand2,
  telemetry and server tests during live acceptance.
- `git diff --check`, full staged name/stat/content review, executable-mode
  inspection and staged secret-string scan passed. Pre-commit ancestry matched
  `ORIG_HEAD=65343df` and `MERGE_HEAD=c3599d3`; both parents are verified again
  after commit.
- Hardware verification above applies to the explicit environment invocation
  of `teleop_dual_rdk_keypoints.sh`. The new wrapper is command-equivalent but
  was added after the live run and received static/offline validation only.

Known limitations:
- DataCollector `6011-6016`, `/api/status`, message growth and
  `malformed_count=0` were not accepted; control success is not collection
  success.
- The bounded 60-second Unitree management-address wait has not been validated
  by a third physical reboot. The Delta autoconnect profile has also not been
  reboot-accepted.
- The RDK sender and glove-interface address are deliberately not installed as
  boot-persistent services/configuration in this commit. An operator must clear
  the workspace, recheck both sides and start the sender explicitly.
- During the final post-stop commit checks, the development host could no longer
  resolve either `192.168.112.106` or `192.168.112.230` on the Delta LAN, so an
  additional remote container rerun was not possible. No network change,
  service restart or hardware command was attempted; current live reachability
  must be rechecked independently from the earlier successful field evidence.

## 2026-08-12 - Wait for the Unitree management address at boot

Base and branch:
- Branch: `data_collect_hand2`.
- Base: `1242320` (`fix: preserve Unitree management IP in Hand2 service`).
- Reboot-under-test boot ID: `17463a66-2a5f-49a5-84fa-4e99ee40bfb9`.

Suggested commit message:
`fix: wait for Unitree management address at boot`

Incident and root cause:
- The management-safe helper ran at `22:40:03`, 18 seconds after boot, while
  `192.168.123.164/24` was still absent. It correctly failed closed, leaving
  management networking untouched, but the left/right service jobs failed with
  their required network dependency.
- `192.168.123.164/24` appeared later even though `network-online.target` had
  already allowed the Hand2 unit to start. The target remained reachable after
  the operator host's direct route was restored.

Changed files:
- `configure_hand2_network.sh` now waits up to 60 seconds for the exact
  management CIDR before failing closed. The wait duration is bounded and can
  be overridden with `HAND2_MANAGEMENT_WAIT_SECONDS`.
- `wuji-hand2-network.service` sets `TimeoutStartSec=75`, allowing the bounded
  wait to finish while dependent hand service jobs remain queued.
- README and SOP document the observed boot ordering, wait behavior, and
  remaining reboot acceptance.

Live recovery and safety:
- Once `.164/24` existed, the failed units were reset and started normally.
  All three became active, `.100/32` was added without modifying `.164/24`, and
  `8765`/`8767` listened again.
- No command client or valid Hand2 qpos was sent; no motion was requested.

Verification:
- `bash -n` and `git diff --check` passed.
- An isolated network namespace added `.164/24` two seconds after helper start;
  the helper waited, then added `.100/32` and both exact routes. Stop removed
  only its own objects and preserved `.164/24` (`DELAYED_ADDRESS_TEST_OK`).
- Invalid wait configuration exited 2; a missing management CIDR with a zero
  wait exited 1 without network mutation.
- The exact helper/unit were installed on the target. Repository and target
  SHA-256 values matched: helper `27692c2c...e234`, unit
  `6b5017b9...a0014c8`.
- A live two-second missing-CIDR test exited 1 after the bounded wait while both
  real addresses stayed intact. A full stop/start cycle preserved `.164`,
  restored `.100` and both routes, reached each Hand2 for 3/3 probes, and left
  all three enabled units active with `NRestarts=0` and ports `8765`/`8767`
  listening.
- `systemd-analyze verify` found no Hand2 unit error; it printed only existing
  unrelated service/varlink warnings. Staged diff review remains required
  immediately before commit.

Known limitation:
- The bounded-wait version has not yet been validated by another real reboot.

## 2026-08-12 - Preserve Unitree management IP during Hand2 startup

Base and branch:
- Branch: `data_collect_hand2`.
- Base: `8f9825b` (`feat: deploy dual Hand2 services on Unitree`).
- Live target: `unitree@192.168.123.164`, Ubuntu 20.04 ARM64, boot ID
  `47a13edf-1f2c-4819-a810-8b93a234b4aa` after the incident reboot.

Suggested commit message:
`fix: preserve Unitree management IP in Hand2 service`

Incident and root cause:
- After the target rebooted, `wuji-hand2-network.service` executed
  `ip address replace 192.168.1.100/32 dev eth0`. This replaced the normal
  `192.168.123.164/24` management address instead of adding a secondary Hand2
  address.
- The resulting `/32` address had routes only to `.110` and `.111`, so it had no
  return path to the operator host. The target emitted traffic as `.100` but did
  not answer ordinary ARP/ping from the operator host.
- Recovery used a short-lived, exact L2 path, restored `.164/24`, disabled the
  faulty units, and removed the temporary operator-host address immediately
  after normal SSH access returned. No Hand2 command frame or motion was sent.

Changed files:
- `deploy/unitree_hand2/configure_hand2_network.sh`: adds an idempotent,
  fail-closed network helper. It requires `192.168.123.164/24` on `eth0`, adds
  only `192.168.1.100/32`, installs the exact `.110`/`.111` host routes, and on
  stop removes only those three Hand2-owned objects.
- `deploy/unitree_hand2/wuji-hand2-network.service`: waits for NetworkManager
  and calls the helper instead of using `ip address replace`.
- `deploy/unitree_hand2/install_services.sh`: installs the helper executable.
- `deploy/unitree_hand2/README.md`: documents the management-address invariant
  and fail-closed behavior.
- `deploy/unitree_hand2/UNITREE_HAND2_SERVICE_SOP.md`: replaces the unsafe
  manual command, adds `.164` acceptance gates, precise incident recovery, and
  the repaired live-validation record.

Live target changes and rollback:
- Installed the repaired helper and unit; the superseded unit is backed up at
  `/var/backups/wuji-hand2/recovery_20260812_2224/`.
- Re-enabled and started `wuji-hand2-network.service`,
  `wuji-hand2@left.service`, and `wuji-hand2@right.service`.
- The running image remains `codex/glove-to-hand-hand2:7abfcdb`; application
  code, Hand2 IPs, control parameters, and DataCollector were not changed.

Verification:
- A deliberately wrong management CIDR returned exit 1 with
  `Refusing Hand2 network setup`; `eth0` remained unchanged.
- Normal start produced both `192.168.123.164/24` and
  `192.168.1.100/32`, plus only the `.110` and `.111` direct `/32` routes.
- Both hands answered 3/3 source-bound probes from `.100`; the operator host
  subsequently reached `.164` for 20/20 probes and each hand for 3/3 probes.
- Stop removed only `.100` and its two routes. Starting only the two Hand2
  instances pulled the network dependency back in; all three units became
  `enabled`/`active`, both containers ran, and ports `8765`/`8767` listened.
- A non-command TCP probe caused logs to state each Hand2 remained disabled;
  no valid qpos was sent.
- Remote and repository SHA-256 hashes for the helper and unit matched.
- `bash -n`, live fail-closed/start/stop checks, `systemd-analyze verify`,
  `git diff --check`, and staged-diff review are required immediately before
  commit.

Known limitations:
- The repaired unit passed a cold-start-equivalent dependency cycle but has not
  yet been validated by a second physical target reboot.
- No valid glove qpos or Hand2 motion was sent. The supervised first-motion
  procedure remains required.
- DataCollector stream growth and malformed counters remain unverified.

## 2026-08-12 - Deploy dual Hand2 services on Unitree

Base and branch:
- Branch: `data_collect_hand2`.
- Deployment code base: `7abfcdb` before this commit.
- Target: `unitree@192.168.123.164`, Ubuntu 20.04 ARM64, kernel
  `5.10.104-tegra`.

Suggested commit message:
`feat: deploy dual Hand2 services on Unitree`

Purpose:
- Package this repository as an offline ARM64 container and run one persistent,
  safety-gated command service for each Wuji Hand2 connected to the Unitree.
- Preserve the target's pre-existing `192.168.1.0/24 via 192.168.123.233`
  route. Reach the factory-addressed hands without changing their persistent IPs
  by adding only `192.168.1.100/32` and direct `.110`/`.111` host routes.
- Make the real-machine deployment reproducible with an operator SOP covering
  preparation, read-only gates, install, acceptance, motion handoff, shutdown,
  evidence capture, and exact rollback.

Changed files:
- `deploy/unitree_hand2/Dockerfile`: builds on the ARM64 Jammy image already on
  the target, installs a pinned offline wheelhouse, imports all native runtime
  modules during build, and copies the exact Hand2 model assets into the
  vendored retarget package.
- `deploy/unitree_hand2/requirements-runtime-arm64.txt`: pins the 33 Python
  3.10/AArch64 wheels used by the deployed runtime, including Wuji SDK
  `2026.8.3`, NumPy `2.2.6`, Pinocchio `3.8.0`, NLopt `2.11.0`, MuJoCo `3.6.0`,
  and the compatible `cmeel-octomap 1.10.0`.
- `deploy/unitree_hand2/prepare_model_assets.sh`: fetches only the Hand2 URDF,
  MJCF and meshes from official `wuji-description` commit
  `7d547ad50ca8cff92d999ae2cc01fc69bcb7c2b6`, verifies critical files, and
  records the source commit in the build context.
- `deploy/unitree_hand2/wuji-hand2-network.service`: adds and removes the
  dedicated `eth0` address plus exact direct routes for `.110` and `.111`,
  without touching unrelated network configuration.
- `deploy/unitree_hand2/wuji-hand2@.service`: defines boot-enabled left/right
  Docker services, image preflight, network ordering, clean container stop, and
  failure restart.
- `deploy/unitree_hand2/run_hand2_container.sh`: maps each instance to the exact
  SN/port, rejects hardware mode unless `ENABLE_HAND2=1`, passes the control,
  slew, watchdog, stiffness and current limits, and runs without a persistent
  writable home directory.
- `deploy/unitree_hand2/wuji-hand2.env` and `.env.example`: record the validated
  production mapping and a fail-closed template. Live mapping is left
  `WH2JA01260717002 @ 192.168.1.110:7447 -> 8765`, right
  `WH2KA01260730030 @ 192.168.1.111:7447 -> 8767`.
- `deploy/unitree_hand2/install_services.sh`: validates the image, installs
  exact launcher/env/unit files, reloads systemd, and enables but deliberately
  does not start the services before read-only acceptance.
- `deploy/unitree_hand2/README.md`: summarizes the deployed topology, main
  operating commands, safety boundary, and offline inputs.
- `deploy/unitree_hand2/UNITREE_HAND2_SERVICE_SOP.md`: adds the copy-ready
  Chinese SOP, including offline wheel/model preparation, image build, SDK scan,
  read-only diagnostics, systemd acceptance, hello-only proof, optional first
  motion protocol, normal shutdown, DataCollector boundary, and exact rollback.

Runtime and safety behavior:
- The target uses image `codex/glove-to-hand-hand2:7abfcdb`; the retained build
  directory is `/home/unitree/hand2-build.VQHyht` and deployment evidence is in
  `/var/backups/wuji-hand2/install_20260812_211314`.
- `wuji-hand2-network.service`, `wuji-hand2@left.service`, and
  `wuji-hand2@right.service` are installed, enabled, and active. The hand
  services listen on TCP `8765`/`8767` and reported `NRestarts=0` at acceptance.
- Control is `200 Hz`, `smooth_tau=0.02 s`, maximum joint velocity `6 rad/s`,
  command timeout `1 s`, `KP=3.5`, `KD=0.1`, and current limit `1.5 A`.
- A service process does not connect/enable its Hand2 until a valid, non-zero,
  side-matched qpos frame arrives. A hello-only client leaves the device
  disabled, and the command watchdog disables it when fresh commands stop.
- Telemetry remains best-effort and non-blocking. The configured collector is
  `192.168.123.222`; its current unavailability does not block control.

Deployment problems resolved:
- The Ubuntu 20.04 host has glibc 2.31, below the current SDK wheel requirement;
  the runtime is therefore isolated in the existing Jammy/glibc 2.35 image.
- The first native import exposed an OctoMap ABI mismatch; pinning
  `cmeel-octomap==1.10.0` made Pinocchio/Coal imports consistent.
- The vendored retarget repository does not contain its model submodule assets;
  the build now injects the exact official model commit, including MuJoCo mesh
  dependencies, before retarget initialization is tested.

Verification:
- Wuji SDK scan discovered left `WH2JA01260717002` at
  `192.168.1.110:7447` and right `WH2KA01260730030` at
  `192.168.1.111:7447`; both reported firmware `2.2.3`, correct handedness,
  `20/20` joints online, and no current joint errors.
- Read-only diagnostics measured left `52.1..60.1 C`, `12.04..12.15 V` and
  right `53.2..61.5 C`, `12.00..12.20 V`.
- Twenty ICMP probes per hand had `0%` loss; average RTT was approximately
  `0.514 ms` left and `0.370 ms` right.
- Both TCP listeners, both containers, all three active/enabled units, image
  identity, routes, and `NRestarts=0` were checked on the live target.
- A hello-only client initialized each retarget configuration without sending
  qpos. Logs stated the hand remained disabled; subsequent read-only diagnostics
  showed `Ready 20/20` on both sides and no Enabled joints.
- In the deployed image,
  `tests.test_hand2_adaptation`, `tests.test_data_collector_telemetry`, and
  `tests.test_hand_qpos_server` ran `29 tests` in `1.297 s`, all passed during
  the final live read-only recheck.
- `systemd-analyze verify` accepted both deployed Hand2 units. It emitted only
  pre-existing executable-bit warnings for unrelated `key_server.service` and
  `chrony.service`.
- `bash -n deploy/unitree_hand2/*.sh`: passed.
- `git diff --check`: passed before staging; the staged diff is checked again
  immediately before commit.

Known limitations:
- No valid glove qpos was sent and no Hand2 motion occurred. Side selection,
  physical joint direction/range, live slew behavior, and the `1 s` disable
  watchdog still require the supervised first-motion procedure in the SOP.
- `192.168.123.222` did not answer ping and TCP `6013-6016` were not open, so
  DataCollector stream growth and `malformed_count=0` remain unverified.
- The target was not rebooted after enablement; boot-time service ordering still
  needs a controlled reboot acceptance window.
- The focused 29-test run did not include the MCAP replay suite. Local broader
  tests had environment-only import gaps; the target image resolved the native
  Hand2 imports used by the running service.

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
