# RDK 三端 Teleop 使用说明

当前架构：

```text
RDK 端：连接 Wuji 手套，发送 keypoints
主机端：接收 keypoints，做 retargeting，发送 qpos
机器人端：连接 Wuji Hand，接收 qpos 并控制灵巧手
```

## 默认地址

- RDK → 主机：`192.168.126.20`
- 主机 → 机器人：默认本地测试 `127.0.0.1`；真实机器人时用 `ROBOT_HAND_HOST=<机器人IP>` 覆盖
- 左手端口：RDK→主机 `8865`，主机→灵巧手 `8765`
- 右手端口：RDK→主机 `8866`，主机→灵巧手 `8767`

如 IP 不同，运行脚本时用环境变量覆盖。

## 1. 机器人端启动灵巧手服务

单手：

```bash
bash teleop_robot_hand.sh
```

双手：

```bash
bash teleop_dual_robot_hand.sh
```

右手单独运行：

```bash
HAND_SIDE=right bash teleop_robot_hand.sh
```

## 2. 主机端启动 retarget bridge

单手：

```bash
ROBOT_HAND_HOST=<机器人IP> bash teleop_host_bridge.sh
```

双手：

```bash
ROBOT_HAND_HOST=<机器人IP> bash teleop_dual_host_bridge.sh
```

右手单独运行：

```bash
HAND_SIDE=right ROBOT_HAND_HOST=<机器人IP> bash teleop_host_bridge.sh
```

## 3. RDK 端启动手套 keypoints 发送

单手：

```bash
HOST_RETARGET_HOST=<主机IP> bash teleop_rdk_keypoints.sh
```

双手：

```bash
HOST_RETARGET_HOST=<主机IP> bash teleop_dual_rdk_keypoints.sh
```

右手单独运行：

```bash
HAND_SIDE=right HOST_RETARGET_HOST=<主机IP> bash teleop_rdk_keypoints.sh
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
