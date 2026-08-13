# Glove to Wuji Hand2

日常完整启动、验收和停止流程见 [WUJI_HAND_TELEOP_SOP.md](WUJI_HAND_TELEOP_SOP.md)。

## 当前真机链路

2026-08-12 已在双手套、RDK X5、Unitree 和两只 Wuji Hand2 上完成实际控制与
安全停机验证。retargeting 直接运行在 Unitree 上，不经过开发机：

```text
左/右 Wuji 手套（192.168.1.100 / 192.168.1.101）
  -> RDK X5（eth0 192.168.1.20，wlan0 192.168.112.230）
  -> Delta Wi-Fi
  -> Unitree wlan0（本次 DHCP 地址 192.168.112.106）
  -> Unitree retargeting 服务（8765 左 / 8767 右）
  -> 左/右 Hand2（192.168.1.110 / 192.168.1.111）
```

Unitree 的管理地址仍为 `192.168.123.164`，Hand2 专用地址为
`192.168.1.100/32`。部署脚本不会替换管理地址。Unitree 上以下服务开机启用：

- `wuji-hand2-network.service`
- `wuji-hand2@left.service`
- `wuji-hand2@right.service`

服务在线不等于 Hand2 已使能。只有收到有效、非零且手侧匹配的第一帧后才连接并
使能对应 Hand2；命令中断超过 1 秒会禁用手。

## RDK X5 准备

RDK 当前 Wi-Fi 地址为 `192.168.112.230`。如需从串口确认地址：

```bash
sudo apt install picocom
sudo picocom -b 115200 /dev/ttyUSB0
ip -4 address show wlan0
```

给 RDK 的手套网口追加运行时地址和两条精确路由；这些命令不会持久化，RDK 重启
后需要重新执行：

```bash
sudo ip address add 192.168.1.20/24 dev eth0 2>/dev/null || true
sudo ip route replace 192.168.1.100/32 dev eth0 src 192.168.1.20
sudo ip route replace 192.168.1.101/32 dev eth0 src 192.168.1.20
sudo ip neigh flush to 192.168.1.100
sudo ip neigh flush to 192.168.1.101
ping -c 3 192.168.1.100
ping -c 3 192.168.1.101
```

环境使用 `wuji_new`。首次安装 retargeting 依赖时：

```bash
cd wuji-retargeting
pip install -r requirements.txt
pip install -e .
```

## 启动与停止

开始前清空双手工作区，确认 Unitree 当前的 Delta DHCP 地址和服务状态。然后在
RDK 仓库根目录前台运行：

```bash
# 不连接机器人，只检查当前 profile
DRY_RUN=1 bash teleop_dual_rdk_unitree_wifi.sh

# 清空工作区并确认手侧后，启动真机控制
UNITREE_WIFI_HOST=192.168.112.106 \
  bash teleop_dual_rdk_unitree_wifi.sh
```

该包装脚本调用 `teleop_dual_rdk_keypoints.sh`，使用 `8765/8767`，并默认设置
`DATA_COLLECTOR_TELEMETRY=0`。当前 DataCollector 尚未完成实流验收，不应把控制
链路成功等同于采集成功。

停止时先在 RDK 终端按 `Ctrl-C`。确认 Unitree 日志出现两侧
`Hand 2 disabled and socket closed` 后，再按需停止机器人端服务：

```bash
sudo systemctl stop wuji-hand2@left.service wuji-hand2@right.service
```

RDK 发送端当前没有安装开机自启动服务；Unitree Wi-Fi 和 Hand2 服务是持久配置。
Delta 使用 DHCP，因此每次重启后应重新读取 Unitree `wlan0` 地址，地址变化时用
`UNITREE_WIFI_HOST=<新地址>` 覆盖。

详细的 RDK 操作、DataCollector 接口和回放说明见 [README_RDK.md](README_RDK.md)。
Unitree 的部署、验收、回滚及本次实测证据见
[deploy/unitree_hand2/UNITREE_HAND2_SERVICE_SOP.md](deploy/unitree_hand2/UNITREE_HAND2_SERVICE_SOP.md)。
