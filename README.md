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

先 dry-run，只读取 glove 并打印目标，不会使能或移动 Wuji Hand：

```bash
conda run -n wuji python /home/user/workspace/wuji/glove_to_hand.py --duration 10 --rate 10
```

确认输出会随手套动作变化后，再实际控制 Wuji Hand。第一次运行建议使用保守参数：

```bash
conda run -n wuji python /home/user/workspace/wuji/glove_to_hand.py \
  --enable-hand \
  --duration 10 \
  --rate 60 \
  --lowpass 15 \
  --gain 0.4 \
  --max-delta 0.25
```

运行前注意：

- `--enable-hand` 模式默认会先把 Wuji Hand 按 `--home-duration` 平滑插值回到 0 位，再开始跟随 glove；
- 程序正常结束或按一次 `Ctrl-C` 退出时，也会按 `--home-duration` 平滑插值回到 0 位，再失能关节；
- 按 `Ctrl-C` 后请等待回零完成，不要连续多次按，否则 Python 进程可能被强制中断，来不及回零；
- 如果不想启动/退出时归零，添加 `--no-home-on-start`；
- Wuji Hand 周围不要有障碍物；
- 准备好按 `Ctrl-C` 停止；
- 启动时保持 glove 自然张开姿态，脚本会把启动姿态作为 neutral；
- 输出里的 `dropped` 表示脚本丢弃了多少个旧 glove 帧；大于 0 是正常的，代表控制使用的是最新帧而不是排队旧帧；
- 如果仍感觉有滤波滞后，可继续提高 `--lowpass`，例如 `--lowpass 20`；如果抖动明显，再降低；
- 四指 J1 侧摆/外展默认会反向修正；如果不需要，添加 `--no-invert-side-sway`；
- `--max-velocity` 限制每个关节目标变化速度，可避免追踪恢复时突然跳到某个角度；
- 可以用 `--joint-gains` 和 `--joint-max-deltas` 单独调整每个关节；
- 如果方向和幅度正常，再逐步增大 `--gain` 和 `--max-delta`。

### 逐关节调参

`--gain` 和 `--max-delta` 是全局默认值。若要单独调整每个关节，使用：

```bash
--joint-gains "20个逗号分隔的数"
--joint-max-deltas "20个逗号分隔的数"
```

顺序是 5×4 矩阵的 row-major：

```text
thumb_j0, thumb_j1, thumb_j2, thumb_j3,
index_j0, index_j1, index_j2, index_j3,
middle_j0, middle_j1, middle_j2, middle_j3,
ring_j0, ring_j1, ring_j2, ring_j3,
pinky_j0, pinky_j1, pinky_j2, pinky_j3
```

例如：四指 J1 侧摆幅度减半、主要弯曲关节保留较大增益：

```bash
conda run -n wuji python /home/user/workspace/wuji/glove_to_hand.py \
  --enable-hand \
  --duration 30 \
  --rate 30 \
  --lowpass 10 \
  --gain -0.8 \
  --max-delta 0.45 \
  --joint-gains "-0.8,-0.8,-0.8,-0.8, -0.8,-0.4,-0.8,-0.8, -0.8,-0.4,-0.8,-0.8, -0.8,-0.4,-0.8,-0.8, -0.8,-0.4,-0.8,-0.8" \
  --joint-max-deltas "0.45,0.45,0.45,0.45, 0.45,0.20,0.45,0.45, 0.45,0.20,0.45,0.45, 0.45,0.20,0.45,0.45, 0.45,0.20,0.45,0.45" \
  --confidence-threshold 0.3 \
  --home-duration 6 \
  --diagnostics
```

