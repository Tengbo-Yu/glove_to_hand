# 双 Wuji Hand2 遥操 SOP

## 1. 链路

```text
双手套 -> RDK X5 -> Delta Wi-Fi -> Unitree retargeting -> 双 Hand2
```

- RDK：`192.168.112.230`
- Unitree：管理地址 `192.168.123.164`，本次 Wi-Fi 地址 `192.168.112.106`
- 手套：左 `192.168.1.100`，右 `192.168.1.101`
- Hand2：左 `192.168.1.110:7447`，右 `192.168.1.111:7447`
- Unitree 接收端口：左 `8765`，右 `8767`

本机只通过 SSH 启停和检查 RDK/Unitree，不转发任何手套或控制数据。

## 2. 启动

先清空双手工作区，再从本机执行。

### 2.1 恢复 RDK 手套网口（RDK 每次重启后执行）

```bash
ssh sunrise@192.168.112.230
sudo ip address add 192.168.1.20/24 dev eth0 2>/dev/null || true
sudo ip link set eth0 up
sudo ip route replace 192.168.1.100/32 dev eth0 src 192.168.1.20
sudo ip route replace 192.168.1.101/32 dev eth0 src 192.168.1.20
ping -c 3 192.168.1.100
ping -c 3 192.168.1.101
ping -c 3 192.168.112.106
exit
```

三项 ping 必须全部无丢包。Unitree 的 `.106` 是 DHCP 地址；若不可达，先登录
`192.168.123.164` 执行 `ip -4 address show wlan0`，并用新地址替换下文 `.106`。

检查两端 Delta Wi-Fi 省电必须关闭（配置已持久化，重启后仍应复查）：

```bash
ssh sunrise@192.168.112.230 'iw dev wlan0 get power_save'
ssh unitree@192.168.123.164 'iw dev wlan0 get power_save'
```

两项都应为 `Power save: off`。如不是，分别在目标机执行：

```bash
sudo nmcli connection modify Delta 802-11-wireless.powersave 2
sudo iw dev wlan0 set power_save off
```

### 2.2 检查 Unitree 接收服务

```bash
ssh unitree@192.168.123.164
systemctl is-active wuji-hand2-network.service \
  wuji-hand2@left.service wuji-hand2@right.service
ss -ltn | grep -E ':(8765|8767)'
exit
```

必须看到三个 `active`，且 `8765/8767` 均在监听。

### 2.3 在 RDK 启动发送端

```bash
ssh sunrise@192.168.112.230
if systemctl --user is-active --quiet wuji-glove-dual.service; then
  echo 'wuji-glove-dual is already running'
  exit
fi
systemctl --user reset-failed wuji-glove-dual.service 2>/dev/null || true
systemd-run --user --unit=wuji-glove-dual \
  --property=Restart=on-failure --property=RestartSec=2s \
  --property=KillMode=control-group \
  --working-directory=/home/sunrise/glove_to_hand \
  /usr/bin/env \
  PATH=/home/sunrise/miniconda3/bin:/home/sunrise/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  HOST_RETARGET_HOST=192.168.112.106 LEFT_PORT=8765 RIGHT_PORT=8767 \
  WUJI_CONDA_ENV=wuji_new DATA_COLLECTOR_TELEMETRY=0 \
  /bin/bash /home/sunrise/glove_to_hand/teleop_dual_rdk_keypoints.sh
systemctl --user status wuji-glove-dual.service --no-pager
exit
```

该服务不是开机自启动；`DATA_COLLECTOR_TELEMETRY=0` 表示本流程不采集数据。

## 3. 验收

RDK 上检查两路发送和连接：

```bash
ssh sunrise@192.168.112.230
journalctl --user -u wuji-glove-dual.service -n 20 --no-pager
ss -tnp | grep -E '192\.168\.112\.106:(8765|8767)'
exit
```

应看到左右 `sent=` 持续增长，以及到 `.106:8765/8767` 的两条 `ESTAB`。

Unitree 上检查两侧接收：

```bash
ssh unitree@192.168.123.164
journalctl -u wuji-hand2@left.service -u wuji-hand2@right.service \
  --since '-2 min' --no-pager | tail -n 80
exit
```

应看到左右正确 SN、`online=20/20`、`Hand 2 enabled` 和持续增长的 `recv=`。
生产日志还会给出 `recv_fps`、`retarget_fps` 和 `control_fps`：现场典型输入
`45-95 Hz`、retarget `35-80 Hz`、平滑下发 `170-200 Hz`。短窗口可波动，但不能
连续 1 秒为 `recv_fps=0`；断流会立即禁用该手并结束本次连接。

Unitree 当前使用 CPU retarget：`RETARGET_MAXEVAL=20` 将 NLopt 单帧迭代限制在
20 次，避免复杂右手姿态跑满默认 50 次。不要为了提高单帧精度直接恢复 50，除非
重新做延时与真手动作验收。

## 4. 停止

先停 RDK 发送端：

```bash
ssh sunrise@192.168.112.230 \
  'systemctl --user stop wuji-glove-dual.service; systemctl --user reset-failed wuji-glove-dual.service'
```

再确认 Unitree 两侧均已禁用：

```bash
ssh unitree@192.168.123.164 \
  "journalctl -u wuji-hand2@left.service -u wuji-hand2@right.service --since '-2 min' --no-pager | grep 'Hand 2 disabled and socket closed'"
```

必须看到左右两条禁用记录。Unitree 接收服务可继续运行并等待下次连接；若需要完全
停掉，再执行：

```bash
ssh unitree@192.168.123.164 \
  'sudo systemctl stop wuji-hand2@left.service wuji-hand2@right.service'
```

## 5. 安全边界

- 同一只手不得同时运行 Wuji Studio 或第二个控制进程。
- 启动前清空工作区；异常时先停 RDK，若 1 秒内未禁用则直接断电。
- 服务/端口在线不代表控制正常，必须检查两侧 `sent=`、`recv=` 和正确 SN。
- 当前 DataCollector 未验收，本 SOP 不启用数据采集。
- Unitree 是 Orin NX，但当前 NLopt/Pinocchio retarget 为 CPU 实现；小型逐帧优化
  直接搬 GPU 收益不确定，且 ZED 已使用 GPU。当前加速点是异步 latest-frame、
  20 次迭代预算和关闭 Wi-Fi 省电。
