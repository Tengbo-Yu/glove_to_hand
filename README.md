# Wuji Glove 控制 Wuji Hand

## 环境

本项目使用 `wuji` conda 环境：

```bash
conda activate wuji
```

如果 SDK 还没有安装：

```bash
conda run -n wuji python -m pip install wuji-sdk wujihandpy
```

## 检查 Wuji Hand 是否被系统识别

Wuji Hand 通过 USB 连接。接好供电和 USB 后，先检查 Linux 是否能看到设备：

```bash
lsusb | grep -i "0483\|wuji"
```

正常会看到类似输出：

```text
Bus 001 Device 015: ID 0483:2000 STMicroelectronics WUJIHAND
```

其中 `0483:2000` 是 Wuji Hand 的 USB VID/PID。

如果没有看到设备：

- 确认 Wuji Hand 已供电；
- 确认 USB 线接到了电脑；
- 如果 USB-C to USB-C 不能识别，换 USB-A to USB-C 线再试；
- 重新插拔 USB 后再次运行 `lsusb`。

## 检查 Python SDK 是否能连接 Wuji Hand

设备被 `lsusb` 识别后，运行：

```bash
conda run -n wuji python -c "import wujihandpy; hand = wujihandpy.Hand(); print('connected'); print('product_sn:', hand.get_product_sn()); print('handedness:', hand.read_handedness())"
```

如果连接成功，会打印：

```text
connected
product_sn: ...
handedness: ...
```

`handedness` 中通常 `0` 表示右手，`1` 表示左手。

## 处理 USB 权限不足

如果 Python SDK 报错类似：

```text
Ignored because device could not be opened: -3 (ERROR_ACCESS)
ConnectionError: Failed to init.
```

说明系统已经识别到设备，但当前用户没有 USB 读写权限。执行：

```bash
sudo mkdir -p /etc/udev/rules.d
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="0483", MODE="0666"' | sudo tee /etc/udev/rules.d/95-wujihand.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

然后拔掉 Wuji Hand 的 USB，再重新插上。

重新测试：

```bash
conda run -n wuji python -c "import wujihandpy; hand = wujihandpy.Hand(); print('connected'); print('product_sn:', hand.get_product_sn()); print('handedness:', hand.read_handedness())"
```

## 查询 USB 序列号

如果电脑上连接了多只 Wuji Hand，需要指定 USB 序列号。查询方式：

```bash
lsusb -v -d 0483:2000 | grep iSerial
```

然后在代码中指定：

```python
import wujihandpy

hand = wujihandpy.Hand(serial_number="你的USB序列号")
```

## 保守动作测试

确认 SDK 可以连接后，再做小幅动作测试。运行前确保手指周围没有障碍物：

```bash
conda run -n wuji python - <<'PY'
import time
import wujihandpy

hand = wujihandpy.Hand()
try:
    hand.write_joint_enabled(True)
    print("enabled")
    hand.finger(1).joint(0).write_joint_target_position(0.3)
    time.sleep(0.5)
    hand.finger(1).joint(0).write_joint_target_position(0.0)
    time.sleep(0.5)
finally:
    hand.write_joint_enabled(False)
    print("disabled")
PY
```

这里的 `finger(1)` 是食指，`joint(0)` 是食指近端关节，`0.3 rad` 是比较保守的小幅度。

## 检查 Wuji Glove 是否连接

Wuji Glove 通过以太网连接。使用 Wuji SDK 扫描：

```bash
conda run -n wuji python -c "from wuji_sdk import SdkManager; m = SdkManager.instance(); print(m.scan())"
```

正常会看到类似：

```text
[DiscoveredDevice(sn='WG1JA03260517019', address='192.168.1.100:50001')]
```

如果连接时报：

```text
Session already exists (0x0013)
```

说明 glove 已经被另一个 SDK 会话占用。处理方式：

- 关闭 Wuji Studio 或其他正在使用 glove 的 Python 脚本；
- 如果没有明显占用进程，给 glove 断电/重新插拔网络连接后再试；
- 重新运行脚本前，等待几秒让设备端 session 清理完成。

## 读取 Wuji Glove 关节角数据

```bash
conda run -n wuji python - <<'PY'
import asyncio
from wuji_sdk import SdkManager

async def main():
    manager = SdkManager.instance()
    glove = manager.auto_connect("glove_0")
    sub = glove.hand_joint_angles().subscribe()
    frame = await sub.recv_async()
    for i, finger in enumerate(frame.fingers):
        print(i, "angles=", list(finger.angles), "confidence=", finger.confidence)
    sub.close()
    manager.disconnect_all()

asyncio.run(main())
PY
```

## Glove 控制 Hand

```bash
机器人
cd glove_to_hand
bash teleop_dual_server.sh

主机
cd glove_to_hand
bash teleop_dual_client.sh
```

## MCAP Replay 到 Wuji Hand

DataCollector 采集到 `/wuji/hand/left/command` 和
`/wuji/hand/right/command` 后，可以不连接手套，直接把 MCAP 里的
`received_qpos_5x4` 重新发给 `hand_qpos_server.py`。

先安装 replay 依赖：

```bash
cd /home/delta/workspace/glove_to_hand
conda run -n wuji pip install -r requirements.txt
```

先启动 hand server dry-run，不加 `--enable-hand`：

```bash
cd /home/delta/workspace/glove_to_hand
conda run -n wuji python hand_qpos_server.py --hand left --port 8765 --no-telemetry
```

另一个终端启动右手 dry-run：

```bash
cd /home/delta/workspace/glove_to_hand
conda run -n wuji python hand_qpos_server.py --hand right --port 8766 --no-telemetry
```

先只检查 MCAP，不发 TCP：

```bash
cd /home/delta/workspace/glove_to_hand
conda run -n wuji python wuji_mcap_replay_client.py \
  /home/delta/workspace/DataCollector/data_collector/datasets/<data_name>/episode_XXXXXX/episode.mcap \
  --dry-run
```

确认帧数和左右手时间轴正常后，再发给 dry-run server：

```bash
cd /home/delta/workspace/glove_to_hand
conda run -n wuji python wuji_mcap_replay_client.py \
  /home/delta/workspace/DataCollector/data_collector/datasets/<data_name>/episode_XXXXXX/episode.mcap \
  --duration-sec 5
```

确认 dry-run server 打印 `recv=` 后，才重启 server 加 `--enable-hand`
做短时间实机 replay。默认 replay 源是 hand server 实际收到的
`hand_command`；如需诊断 retarget 生成端，可加 `--source glove_command`。


### 设置连接网口
```bash
ip route get 192.168.1.100
ip route get 192.168.1.101

ifconfig

sudo ip route replace 192.168.1.100/32 dev enxe466e5832575(对应网口编号) src 192.168.1.xxx(对应设置的本机ip)
sudo ip neigh flush to 192.168.1.100
```
