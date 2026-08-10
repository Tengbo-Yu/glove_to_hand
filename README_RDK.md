# RDK → Hand 2 Teleop 使用说明

host1 和 robot 运行在同一台主机时，推荐使用两进程直连架构：

```text
RDK 小主机 + 右手套
    │ keypoints, TCP 10.1.10.166:8866
    ▼
本机：Hand 2 retargeting + wuji-sdk → 右侧 Wuji Hand 2 WH2KA01260730030
```

这与官方 `teleop_real.py` 的单循环结构一致，省去了本机
`host_retarget_bridge → hand_qpos_server` 的第二跳 TCP 和第二层低通滤波。

## 默认地址

- RDK → host1：当前默认 `10.1.10.166`
- 左手端口：RDK→主机 `8865`
- 右手端口：RDK→主机 `8866`

如本机地址变化，在 RDK 上用 `HOST_RETARGET_HOST=<本机可达IP>` 覆盖。只有配置了
专用直连网后才使用旧的 `192.168.126.20` 地址。

所有 Python 入口默认使用 `wuji_new`，协议为 `glove-qpos-v2`。旧版
Hand1/USB 客户端与 v1 qpos 会被拒绝。

## 单右手推荐启动顺序

### 1. 本机：启动直连控制

以下脚本收到第一帧有效右手关键点后会使能机械手。运行前先清空机械手
工作空间：

```bash
HAND_SIDE=right RIGHT_HAND_SN=WH2KA01260730030 \
bash teleop_direct_hand.sh
```

服务绑定 `10.1.10.166:8866`。启动时只监听；收到第一帧有效右手关键点后
才连接并使能 Hand 2。首帧从实测关节角按最大 `6 rad/s` 接入；默认以
200 Hz 输出并使用 `0.02 s` 轻量插值。`KP=3.5`、`KD=0.1`、电流限制
`1.5 A`；连续 1 秒没有有效命令会自动失能。

### 2. RDK 小主机：启动右手套发送

```bash
HAND_SIDE=right HOST_RETARGET_HOST=10.1.10.166 \
RIGHT_GLOVE_SN=WG1KA03260512012 bash teleop_rdk_keypoints.sh
```

发送端默认 120 Hz 轮询并持续发送最近帧，使用官方 `hand_skeleton` 数据流；
持续帧让 retarget 低通在 SDK 新帧之间继续收敛，避免输出呈阶梯状。
手套固件 v0.11.2 不一定提供 `algorithms.emf_poses.rate_divider`，因此脚本
默认不再写这个资源；实际传感器更新率以延迟诊断输出的 `fresh_fps` 为准。
这两个脚本都默认右手，因此当前地址和序列号不变时也可直接运行。

如需分段检查延迟，在两个终端都增加 `DEBUG_LATENCY=1`：

```bash
# 本机
DEBUG_LATENCY=1 HAND_SIDE=right bash teleop_direct_hand.sh

# RDK（需要同步本仓库中相同版本的 glove_qpos_client.py 和 qpos_protocol.py）
DEBUG_LATENCY=1 HAND_SIDE=right bash teleop_rdk_keypoints.sh
```

主要观察以下字段：

- `fresh_fps`、`fresh_period_ms`：真正的新手套帧率和断帧时间；
- `app_rtt_ms`：从 RDK 发送、主机排队并完成 retarget、再回到 RDK 的 RTT；
- `transport_rtt_residual_ms`：扣除主机排队和处理后的往返传输余量；
- `server_queue_ms`、`server_retarget_ms`：主机控制排队和 retarget 解算；
- `hand_write_ms`：向 Hand 2 发布命令的耗时。

这里的 RTT 使用 RDK 自己的单调时钟测量，不要求两台机器时钟同步。
`clock_wire_age_ms` 只用于参考；若两机没有 PTP/NTP 同步，不能把它当作
准确的单向网络延迟。

停止时先在 RDK 端按 `Ctrl-C`；本机命令看门狗会使 Hand 2 失能，再停止
本机服务。

## Hammerhead DataCollector 采集

`data_collect_hand2` 保持当前 DataCollector `hammerhead` 配置的六路接口：

| 数据边界 | 左手 | 右手 | MCAP topic |
| --- | ---: | ---: | --- |
| RDK 手套输入 | 6011 | 6012 | `/wuji/glove/{left,right}/command` |
| 主机 Hand 2 目标/下发命令 | 6013 | 6014 | `/wuji/hand/{left,right}/command` |
| Hand 2 实测关节状态 | 6015 | 6016 | `/wuji/hand/{left,right}/state` |

消息使用 DataCollector 的 `[JSON header, msgpack payload]` ZMQ multipart
格式，并以 `NOBLOCK` 发送；DataCollector 未运行或队列满时只增加
`telemetry_dropped_count`，不会阻塞手套、retarget 或 Hand 2 控制。

RDK 脚本默认把手套 telemetry 发到 `HOST_RETARGET_HOST`。如果 DataCollector
在另一台机器，显式设置：

```bash
DATA_COLLECTOR_HOST=<DataCollector主机IP> \
HAND_SIDE=right HOST_RETARGET_HOST=10.1.10.166 \
bash teleop_rdk_keypoints.sh
```

主机上的 `hand_qpos_server.py` 默认发到 `127.0.0.1`。分离部署时同样用
`DATA_COLLECTOR_HOST=<IP>` 覆盖。仅调试控制、不采集时，可在脚本前设置
`DATA_COLLECTOR_TELEMETRY=0`，或对 Python 入口传 `--no-telemetry`。

开始动作前先确认 Hammerhead DataCollector 已监听六个端口：

```bash
ss -ltnp '( sport >= :6011 and sport <= :6016 )'
curl -s http://127.0.0.1:8080/api/status
```

采集后应检查 MCAP 中三类 topic 都有非零消息，而不只检查服务状态。
`glove` payload 保存 21×3 原始 keypoints；`hand command` 同时保存 retarget
目标 `received_qpos_5x4` 和本次平滑下发值 `applied_qpos_5x4`；`hand state`
保存最近的 `actual_qpos_5x4` 以及 `feedback_fresh`。所有 Hand 2 qpos 都明确
标注 `hand2_device_thumb_to_pinky` 顺序。

手套 topic 的 `source_timestamp_ns` 来自 RDK 系统时钟；hand command/state
来自运行 server 的主机系统时钟。payload 会标明 clock domain，但不会假定
RDK 与主机已经同步。未用 PTP/NTP 验证前，不要把跨机时间差当作准确单向延迟。

### MCAP 回放（先 dry-run）

默认从 `/wuji/hand/*/command` 的 `received_qpos_5x4` 回放，并使用当前
`glove-qpos-v2` 协议；左右 server 默认端口分别是 `8765`、`8767`。

```bash
python wuji_mcap_replay_client.py \
  /path/to/episode.mcap --dry-run

# 只向不使能硬件的 server 做 5 秒 TCP 验证
python hand_qpos_server.py --hand right --port 8767 --no-telemetry
python wuji_mcap_replay_client.py /path/to/episode.mcap \
  --duration-sec 5
```

只有在 dry-run server 已验证帧数、手侧、关节顺序和轨迹范围后，才可清空
机械手工作空间并另行显式使用 `--enable-hand`。回放工具本身不会绕过 server
的首帧使能门和命令超时保护。

## Retargeting 标定

右手默认使用 [config/hand2_right_teleop.yaml](config/hand2_right_teleop.yaml)，
它保留官方 Hand 2 模型、关节命名和损失项，仅将 `norm_delta` 调为 `0.025`、
`lp_alpha` 调为 `0.8`。在连接手套且有图形界面的 RDK 上运行：

```bash
HAND_SIDE=right bash teleop_tune_hand2.sh
```

窗口中橙色是原始手套骨架，青色是 `segment_scaling` 后的目标，白色是
Hand 2 正向运动学。先逐指调整 YAML 中的 `segment_scaling`，使青色与白色
指尖贴合；再调 `norm_delta` 和 `lp_alpha`。配置会在保存后自动重载。

### 大拇指 J4 不动的分层检查

先停止 teleop，在 RDK 上只观察手套和 retarget 目标（不会连接 Hand 2）：

```bash
conda run --no-capture-output -n wuji_new python \
  thumb_retarget_diagnostic.py --stream hand_skeleton --duration 10
```

十秒内反复只弯曲大拇指末端。脚本会报告输入 IP 弯曲角跨度和 Hand2 J4
目标跨度，并说明问题位于手套输入、retarget 还是硬件层。若输入几乎不变，
再比较：

```bash
conda run --no-capture-output -n wuji_new python \
  thumb_retarget_diagnostic.py --stream offline_hand_skeleton --duration 10
```

若目标 J4 明显变化但实物不动，在清空工作空间后可单独测试物理关节：

```bash
conda run --no-capture-output -n wuji_new python wuji_hand_test.py \
  --hand right --hand-sn WH2KA01260730030 \
  --enable-motion --joint 3 --delta 0.10
```

这里使用从 0 开始的索引，大拇指 J4 是索引 `3`；索引 `4` 是食指 J1。

## 旧三进程兼容模式

只有 host1 和 robot 分属不同机器，或需要定位各段问题时，才使用：

```bash
HAND_SIDE=right bash teleop_robot_hand.sh
HAND_SIDE=right ROBOT_HAND_HOST=127.0.0.1 bash teleop_host_bridge.sh
HAND_SIDE=right HOST_RETARGET_HOST=10.1.10.166 bash teleop_rdk_keypoints.sh
```

同机运行时不要同时启动 `teleop_direct_hand.sh` 和
`teleop_host_bridge.sh`，二者都会占用 `8866`。

## 双手或分离 robot 主机

双手仍使用：

```bash
ENABLE_HAND2=1 bash teleop_dual_robot_hand.sh
ROBOT_HAND_HOST=<机器人IP> bash teleop_dual_host_bridge.sh
HOST_RETARGET_HOST=<主机IP> bash teleop_dual_rdk_keypoints.sh
```

dual 参数已与单手版本对齐：手套侧 `120 Hz`，Hand 2 侧 `200 Hz`、
`smooth_tau=0.02 s`、`max_joint_velocity=6 rad/s`、命令看门狗 `1 s`、
`KP=3.5`、`KD=0.1`、电流限制 `1.5 A`。retarget 不再统一覆盖为 `0.6`，
而是与单手一样保留左右手各自 YAML 中的 `lp_alpha` 和 `norm_delta`。

## 有线网口配置

需要配置专用有线网段时：

```bash
bash setup_wired_network.sh local   # RDK 端
bash setup_wired_network.sh host    # 主机端，当前默认使用 enp8s0
```

如果网口名不同：

```bash
bash setup_wired_network.sh local <网口名>
bash setup_wired_network.sh host <网口名>
```

如果主机与 Hand 2、RDK 接在同一个交换机上，不要使用会替换接口地址的
`host` 模式。应在 Hand 2 接口上追加第二个网段：

```bash
# 保留主机到 Hand 2 的 192.168.1.200/24，同时追加 192.168.126.20/24
bash setup_wired_network.sh host-shared enx6c1ff7d6f986
```

该模式只做运行时追加，不会删除现有地址；重启后需要重新执行。当前拓扑中，
Hand 2 继续使用 `192.168.1.111`，RDK 使用 `192.168.126.10`。

## RDK 双网口建议配置

RDK 端建议两个有线网口分开使用：

```text
eth0             -> Wuji 手套网络，192.168.1.20/24
enx00e04c584b78  -> RDK 到主机，192.168.126.10/24
wlan0            -> SSH/普通网络，10.1.10.x
```

配置 RDK 到主机的专用网口：

```bash
bash setup_wired_network.sh local enx00e04c584b78
```

恢复/设置 Wuji 手套网口：

```bash
sudo ip addr del 192.168.126.10/24 dev eth0 2>/dev/null || true
sudo ip addr add 192.168.1.20/24 dev eth0 2>/dev/null || true
sudo ip link set eth0 up
sudo ip route replace 192.168.1.100/32 dev eth0 src 192.168.1.20
sudo ip route replace 192.168.1.101/32 dev eth0 src 192.168.1.20
sudo ip neigh flush to 192.168.1.100
sudo ip neigh flush to 192.168.1.101
```

检查路由：

```bash
ip route get 192.168.126.20   # 应该走 enx00e04c584b78
ip route get 192.168.1.100    # 应该走 eth0
ip route get 192.168.1.101    # 应该走 eth0
```

## 检查

```bash
ping <主机IP或机器人IP>
ip route get <主机IP或机器人IP>
```

确认 teleop 数据走有线网口，SSH 仍走原来的 `10.1.10.x` 网络。

机器人端还需要一块位于 `192.168.1.0/24` 的网卡连接 Hand 2。右手默认
静态 IP 为 `192.168.1.111`，但连接参数应优先使用 SN 或 SDK 扫描返回的
完整地址，不要假定端口固定。该网卡与 `192.168.126.0/24` 的三端传输网卡
应使用不同物理接口或明确的独立路由。
