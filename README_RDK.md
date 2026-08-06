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
200 Hz 输出并使用 `0.02 s` 轻量插值。`KP=3.0`、`KD=0.1`、电流限制
`1.0 A`；连续 1 秒没有有效命令会自动失能。

### 2. RDK 小主机：启动右手套发送

```bash
HAND_SIDE=right HOST_RETARGET_HOST=10.1.10.166 \
RIGHT_GLOVE_SN=WG1KA03260512012 bash teleop_rdk_keypoints.sh
```

发送端默认 120 Hz 轮询并持续发送最近帧，使用官方 `hand_skeleton` 数据流；
持续帧让 retarget 低通在 SDK 新帧之间继续收敛，避免输出呈阶梯状。
启动时还会把手套持久化的 `emf_poses_rate_divider` 明确设为 `1`，确保
`hand_skeleton` 恢复约 120 Hz，而不是 divider=4 时的约 30 Hz。
这两个脚本都默认右手，因此当前地址和序列号不变时也可直接运行。

如需分段检查延迟，在两个终端都增加 `DEBUG_LATENCY=1`。RDK 会报告 SDK
取帧、缓存和网络发送耗时，本机会报告网络帧年龄、重定向和 Hand 2 写入耗时。

停止时先在 RDK 端按 `Ctrl-C`；本机命令看门狗会使 Hand 2 失能，再停止
本机服务。

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

## 有线网口配置

需要配置专用有线网段时：

```bash
bash setup_wired_network.sh local   # RDK 端
bash setup_wired_network.sh host    # 主机端
```

如果网口名不同：

```bash
bash setup_wired_network.sh local <网口名>
bash setup_wired_network.sh host <网口名>
```

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
