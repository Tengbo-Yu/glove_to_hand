# RDK 三端 Teleop 使用说明

当前单右手架构：

```text
RDK 小主机 + 右手套
    │ keypoints, TCP 10.1.10.166:8866
    ▼
本机 host1：Hand 2 retargeting
    │ device-order qpos, TCP 127.0.0.1:8767
    ▼
本机 robot：wuji-sdk → 右侧 Wuji Hand 2 WH2KA01260730030
```

## 默认地址

- RDK → host1：当前默认 `10.1.10.166`
- host1 → robot：同一台主机，固定使用 `127.0.0.1`
- 左手端口：RDK→主机 `8865`，主机→灵巧手 `8765`
- 右手端口：RDK→主机 `8866`，主机→灵巧手 `8767`

如本机地址变化，在 RDK 上用 `HOST_RETARGET_HOST=<本机可达IP>` 覆盖。只有配置了
专用直连网后才使用旧的 `192.168.126.20` 地址。

所有 Python 入口默认使用 `wuji_new`，协议为 `glove-qpos-v2`。旧版
Hand1/USB 客户端与 v1 qpos 会被拒绝。

## 单右手启动顺序

### 1. 本机终端 1：启动 robot

以下脚本会使能机械手，因此必须显式设置 `ENABLE_HAND2=1`。

```bash
HAND_SIDE=right ENABLE_HAND2=1 \
RIGHT_HAND_SN=WH2KA01260730030 bash teleop_robot_hand.sh
```

服务只绑定 `127.0.0.1:8767`。启动时仅监听；收到 host1 转发的首个有效右手
qpos 后才连接并使能 Hand 2。首帧从实测关节角平滑接入，默认最大速度
`2 rad/s`；连续 1 秒没有有效命令会自动失能。

### 2. 本机终端 2：启动 host1

```bash
HAND_SIDE=right ROBOT_HAND_HOST=127.0.0.1 bash teleop_host_bridge.sh
```

host1 监听所有本机接口的 `8866`，允许 RDK 断开后重新连接。

### 3. RDK 小主机：启动右手套发送

```bash
HAND_SIDE=right HOST_RETARGET_HOST=10.1.10.166 \
RIGHT_GLOVE_SN=WG1KA03260512012 bash teleop_rdk_keypoints.sh
```

三个脚本现在都默认右手，因此当前地址和序列号不变时也可分别直接运行。
所有进程使用实时、无缓冲输出，便于观察连接和看门狗状态。

停止时先在 RDK 端按 `Ctrl-C`；host1 会关闭本地 qpos 连接，robot 随即失能
Hand 2。然后再停止 host1 和 robot。

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
