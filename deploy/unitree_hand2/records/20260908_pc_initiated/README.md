# 2026-09-08 Wuji手遥测去除固定PC地址：部署记录

## 仓库范围

本次代码仓库只修改glove_to_hand。基线d8d3ebf，功能提交7a5ba0d、部署结果提交07d06ef。teleoperation中已有robot_hand_adapter.py和deltacollect-hand-telemetry-tunnel.service被直接复用，没有修改该仓库或239的已安装脚本。DataCollector/G1_deploy/SOMA-online没有变更。本次不包含此前三台PC离线整合分支。

本目录记录实际执行过的单机部署脚本和验证证据，不是可随意执行的通用安装器。脚本包含目标机器身份、固定起始配置和备份目录检查；重复执行apply会拒绝已存在的备份。运行root脚本前必须阅读源码、确认目标和当前无录制/手部控制。归档这些文件不会再次部署。

## 起因和最终链路

原机器人/etc/default/wuji-hand2及左右Docker进程均为DATA_COLLECTOR_HOST=10.1.10.239。机器人访问该地址实际走wlan0，源地址10.1.10.227。用户要求PC主动连接固定机器人地址192.168.123.164，换数采PC时不再改机器人目的地址。

最终Hand2进程保留ZeroMQ PUSH和双段消息格式，改发127.0.0.1；239主动建立SSH到unitree@192.168.123.164，四个-R回环转发把消息送到239本机DataCollector。实测SSH源地址192.168.123.138，走机器人侧有线网络。控制命令端口8765/8767本来就是客户端连接机器人，因此无需改控制协议。

| 内容 | 开始前 | 完成后 |
| --- | --- | --- |
| 机器人遥测目标 | 10.1.10.239 | 127.0.0.1 |
| 239隧道 | enabled/inactive | enabled/active，检查时0次自动重启 |
| 机器人6013–6016 | 无隧道监听 | SSH持有127.0.0.1监听 |
| Hand2运行服务 | 左右均active | 保留左右active，仅在无控制连接时重启加载env |
| 239 DataCollector | PID2540329 | 原PID保留，未重启，Idle |
| 镜像、SN、SDK、控制参数 | 原值 | 保持原值，kp=6、kd=0.2 |

6013/6014为左/右手命令遥测，6015/6016为左/右实测状态。机器人镜像ID和手SN见robot-verification.json。原始手套6011/6012属于RDK发送端，本次未修改。

## 部署过程与持久化

1. 只读备份7份现役配置/脚本，hash见before/manifest.json；原始文件保留在私有本机证据目录，未发布其内容。
2. 核验机器人identity=jetson-1794225001823，accepted-config无既有漂移，8765/8767无ESTABLISHED客户端；239没有recording/start/stop进行中。
3. pc_apply.py保存239服务原状态并启动已有系统隧道；确认机器人四个回环监听出现。
4. robot_apply.py保存原env、accepted.json和服务状态，仅替换DATA_COLLECTOR_HOST并accept该文件，然后重启原本active的左右Hand2服务。
5. robot_verify.py确认只有/etc/default/wuji-hand2对应guard条目变化、真实容器env生效且镜像/SN/kp/kd不变。
6. 对四个真实端口执行ZMTP握手，确认端到端接到239接收端；没有send数据载荷。

239无需修改系统unit文件，无需daemon-reload、重启DataCollector或升级Python/SDK。机器人启动前的配置保护现在接受回环地址，不会自动恢复旧PC地址。已持久配置但未做整机重启验收。

## 验证证据和边界

- tests/test_data_collector_telemetry.py及tests/test_hand2_live_telemetry.py：6项通过，假SDK/本地socketpair，无真实手部控制。
- test_isolated_tunnel.py：在隔离26014端口经过真实PC239→机器人SSH连接，收到双段消息；header、timestamp和payload逐字节一致。结果tunnel-test.json。
- test_existing_adapter.py：临时运行现役适配器，完成机器人profile和SSH指纹校验，建立6013–6016回环转发，随后正常退出。结果adapter-test.txt。
- test_live_handshake.py：部署后的4个生产端口全部EVENT_HANDSHAKE_SUCCEEDED；data_messages_sent=0。结果live-handshake.json。没有伪造生产遥测或写入episode。
- robot-verification.json：部署后的容器和配置保护验证结果。

未验证真实右手动作、连续新鲜状态、MCAP完整性、掉线重连或换另一台PC。只有右手的机器人仍保留原双手service/profile/数采门禁，本次没有解决缺左手数据可能阻止录制的问题。上述通信测试不能解释为单右手遥操数采已经完整验收。

## 回退和换PC

两台远端备份目录均为/var/backups/wuji-pc-initiated-20260908。机器人保存原env、原accepted.json和服务状态；239保存隧道原状态。完整私有证据在/home/descfly/Documents/Codex/2026-09-08/wuji-pc-initiated-telemetry。

在确认无录制及手部客户端后，先执行robot_rollback.py恢复旧env并仅更新它的guard条目、恢复原服务状态，再执行pc_rollback.py恢复239隧道原运行状态。不会用整份旧accepted.json覆盖后来其他配置条目。仍需人工复核没有其他新配置变化。回退脚本已审阅和语法校验，实际生产回退未执行。

remote_admin.py通过交互getpass读取sudo密码、SSH stdin传给sudo，不保存密码。发布内容不含sudo密码、SSH私钥或accepted.json原始备份。

换PC时先停止旧PC隧道，再启动新PC同类隧道。新PC需已有SSH授权、同样的身份校验能力及6013–6016数采接收端。回环端口只能由一个PC持有，不能同时连接多台采集PC；ExitOnForwardFailure避免默默抢占。

发布的adapter-test.txt仅去除末尾空行；原始日志仍保存在私有证据目录。脚本内容未改写。
