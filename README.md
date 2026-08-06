# Wuji Glove → Wuji Hand 2

本项目已迁移到 **Wuji Hand 2（以太网）**。旧版 Wuji Hand 的
`wujihandpy`、USB 串口、USB serial 和 `realtime_controller` 均不再用于活动链路。

当前验证组合：

- Wuji Hand 2 固件：`2.2.3`
- `wuji-sdk==2026.8.3`
- `wuji-retargeting==2026.8.3`
- Conda 环境：`wuji_new`

## 1. 安装与模型

```bash
git -C wuji-retargeting submodule update --init --recursive
conda run -n wuji_new python -m pip install -r requirements.txt
```

`pin 3.8.0` 的 wheel 依赖 urdfdom 4 / tinyxml2 10 ABI；不要删除
`requirements.txt` 中的 `cmeel-urdfdom==4.0.1` 和
`cmeel-tinyxml2==10.0.0` 约束。

验证导入：

```bash
conda run -n wuji_new python -c "from wuji_retargeting import Retargeter; print('retargeting OK')"
```

## 2. Hand 2 网络与只读检查

Hand 2 使用静态地址：左手通常为 `192.168.1.110`，右手通常为
`192.168.1.111`。主机对应网卡必须位于 `192.168.1.0/24`。

优先按 SN 或扫描结果连接，不要硬编码端口；本机右手扫描结果为
`WH2KA01260730030 @ 192.168.1.111:7447`。

只读检查（不会使能）：

```bash
conda run -n wuji_new python wuji_hand_test.py --hand right
```

白色呼吸灯表示全部在线关节就绪、未使能。只有显式传入运动开关才会使能：

```bash
conda run -n wuji_new python wuji_hand_test.py \
  --hand right --enable-motion --joint 3 --delta 0.05
```

`--joint` 使用从 0 开始的设备索引，因此大拇指 J4 是 `3`，索引 `4`
已经是食指 J1。

## 3. 单机手套重定向

先进行干跑，只打印 Hand 2 设备顺序的关节目标：

```bash
conda run -n wuji_new python glove_to_hand.py \
  --hand right --glove-sn <RIGHT_GLOVE_SN> --duration 10
```

确认关键点、手性、关节顺序和工作空间后，才显式使能：

```bash
conda run -n wuji_new python glove_to_hand.py \
  --hand right --glove-sn <RIGHT_GLOVE_SN> \
  --hand-sn WH2KA01260730030 \
  --enable-hand --current-limit 1.0 --duration 10
```

默认配置自动选择：

```text
config/hand2_right_teleop.yaml
adaptive_analytical_wuji_glove_wuji_hand_2_left.yaml
```

代码会按关节名验证并执行 `URDF → MJCF/设备` 重排；映射失败时拒绝启动，
不会把未验证顺序的 qpos 发给硬件。

## 4. RDK 到 Hand 2 链路

host1 与 robot 在同一台主机时，推荐使用官方同类的单循环直连结构：

```text
RDK（手套 keypoints） → 本机（Hand 2 retargeting + Hand 2）
```

本机先启动；脚本收到第一帧有效命令后才使能 Hand 2：

```bash
HAND_SIDE=right bash teleop_direct_hand.sh
```

RDK 端：

```bash
HAND_SIDE=right HOST_RETARGET_HOST=10.1.10.166 bash teleop_rdk_keypoints.sh
```

所有脚本默认使用 `wuji_new`；可通过 `WUJI_CONDA_ENV=<name>` 覆盖。
网络协议为 `glove-qpos-v2`，qpos 帧必须携带正确手性并声明
`joint_order=device`，旧版 v1 客户端会被明确拒绝。

若 host1 和 robot 分属不同机器，仍可使用 `teleop_host_bridge.sh` 与
`teleop_robot_hand.sh` 的三进程兼容模式。

双手与专用有线网配置见 [README_RDK.md](README_RDK.md)。

## 5. 主要入口

| 文件 | 用途 |
|---|---|
| `hand2_backend.py` | Hand 2 发现、连接、诊断、MIT 参数、使能和命令发布 |
| `retargeting_hand2.py` | Hand 2 配置及 URDF→设备关节顺序验证 |
| `wuji_glove_input.py` | 手套输入、offline skeleton 和延迟统计 |
| `glove_to_hand.py` | 单机单手 |
| `glove_to_hand_dual.py` | 单机双手 |
| `glove_qpos_client.py` | RDK keypoints/qpos 客户端 |
| `host_retarget_bridge.py` | 主机重定向桥 |
| `hand_qpos_server.py` | 机器人端 Hand 2 服务 |
| `teleop_direct_hand.sh` | 同机低延迟直连入口 |
| `teleop_tune_hand2.sh` | 官方 Hand 2 可视化标定工具入口 |

停止时默认直接失能，不自动回零。需要回零时显式传
`--home-on-shutdown`；回零本身也是运动，请先确保工作空间安全。
