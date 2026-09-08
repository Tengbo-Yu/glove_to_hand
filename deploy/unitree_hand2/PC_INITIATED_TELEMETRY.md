# PC-initiated Hand2 telemetry

The robot no longer needs the recording PC's management IP. Its Hand2 containers
send the existing nonblocking ZeroMQ PUSH streams to `127.0.0.1:6013–6016`.
The recording PC initiates an authenticated SSH connection to
`unitree@192.168.123.164`, using the existing DeltaCollect
`deltacollect-hand-telemetry-tunnel.service` and robot profile host-key validation.
Remote loopback forwards carry the unchanged streams to the connecting PC's
local DataCollector PULL ports. Robot command listeners8765/8767 already accept
client-initiated TCP; their protocol and hand hardware selection are unchanged.

| Stream | Robot loopback and PC collector port |
| --- | --- |
| Left hand command | 6013 |
| Right hand command | 6014 |
| Left measured hand state | 6015 |
| Right measured hand state | 6016 |

This transports telemetry, not Hand2 control commands. SSH remote listeners bind
to127.0.0.1, not all robot interfaces. Only one recording PC can own these
listeners at a time; ExitOnForwardFailure rejects a second owner. When switching
PCs, stop the first PC tunnel, then start the new PC tunnel to the same robot IP.
The new PC needs authenticated robot SSH access and local collector listeners.
No change to robot telemetry destination is needed for that switch.

The raw glove6011/6012 publishers are separate RDK processes, not outputs of the
onboard Hand2 service. This change does not relocate or reconfigure those sources.
A single physical right hand also does not automatically change the advertised
left/right service list or the collector's required-hand-stream policy.

## Field deployment scope

On PC239 the existing system service already targets192.168.123.164 and was
enabled but inactive. Start that service; no DataCollector restart or package
update is needed. Verify all four remote loopback listeners exist and are owned
by the new SSH connection before changing the robot sender destination.

On the robot, change only DATA_COLLECTOR_HOST in /etc/default/wuji-hand2 from
the old PC address to127.0.0.1. Preserve image, SN, ports, SDK, kp/kd and all
control parameters. This robot has an external accepted-config guard: back up
its accepted.json, check for pre-existing drift, and explicitly accept only the
changed env file before restarting the idle Hand2 services. Otherwise the next
service start would restore the old address.

Never restart a Hand2 service with an active command client or while recording.
Do not send a valid hand command merely to test transport. Without a real command
session, the lazy Hand2 server may publish no telemetry; listening sockets alone
are not proof of measured hand data.

## Validation and rollback

2026-09-08 preparation:6 hardware-free telemetry regressions passed. A real
PC239 -> robot SSH reverse-forward test on isolated port26014 delivered both
ZeroMQ multipart frames byte-for-byte to an isolated PC receiver. No production
telemetry packets were injected and no hardware command was sent.

Before deployment, save the robot env/accepted-config manifest and both hosts'
service states. To roll back, ensure recording and hand clients are idle,
restore the prior env and its accepted entry, restart only services previously
active, and restore the PC tunnel's previous active/enabled state. Keep backup
files until after real collection acceptance.

Field evidence and prepared transaction scripts are stored at:
/home/descfly/Documents/Codex/2026-09-08/wuji-pc-initiated-telemetry

Field activation completed on2026-09-08: the239 system tunnel is active/enabled,
robot containers use127.0.0.1, and only the env entry changed in accepted-config.
All four live ports passed end-to-end ZMTP handshake without sending payloads.
Collector PID was preserved; image/SN/kp/kd were verified unchanged.
Real hand-motion/fresh-telemetry acceptance has not been performed.
