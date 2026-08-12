# Unitree 双 Wuji Hand2 真机服务部署 SOP

本文用于在 `unitree@192.168.123.164` 上部署本仓库的左右 Hand2 命令服务。
它同时记录 2026-08-12 的实机验收结果。命令中的密码不写入仓库；SSH 和
`sudo` 密码由操作者交互输入。

## 1. 已验证范围与安全边界

本次已验证：ARM64 离线镜像构建、两只手的 SDK 扫描和只读诊断、网络路由、
systemd 自启动、左右 TCP 监听、hello-only 不使能、命令看门狗代码路径以及
容器内 29 个聚焦测试。

本次**没有**发送手套驱动的有效关节命令，也没有做真机运动验收；服务在线不等于
运动链路已经验收。当前 `192.168.123.222:6013-6016` 不可达，因此
DataCollector 实流验收也仍待完成。

安全规则：

1. 同一只手只允许一个 Wuji Studio/SDK 控制进程。启动服务前退出 Wuji Studio。
2. 在 RDK 发送有效帧前清空机械工作区，并安排操作者随时断电。
3. 服务只有在 `/etc/default/wuji-hand2` 中 `ENABLE_HAND2=1` 时才允许进入硬件模式；
   即使服务已运行，hello-only 连接仍不会使能手。
4. 第一帧有效、非零且左右匹配的命令才会触发连接和使能。新命令中断超过
   `1.0 s` 时看门狗会禁用手。
5. 停机顺序必须是：先停 RDK 命令发送端，等待看门狗禁用，再停 Unitree 服务。

## 2. 本次目标拓扑

| 角色 | SN / 地址 | 服务端口 |
| --- | --- | --- |
| 左 Hand2 | `WH2JA01260717002` / `192.168.1.110:7447` | `8765/tcp` |
| 右 Hand2 | `WH2KA01260730030` / `192.168.1.111:7447` | `8767/tcp` |
| RDK 命令发送端 | 连接 `192.168.123.164` | `8765`、`8767` |
| DataCollector（待验收） | `192.168.123.222` | `6013-6016` |

本次**不需要修改 Hand2 IP**。目标机原有
`192.168.1.0/24 via 192.168.123.233`，因此服务只给 `eth0` 增加
`192.168.1.100/32`，并为 `.110`、`.111` 增加两条更具体的直连 `/32` 路由；
原有路由不被覆盖。只有该直连方案确认不可用时，才按 Wuji 文档的
[`IPSet/Get`](https://docs.wuji.tech/docs/zh/wuji-hand/latest/sdk-reference/#215-ipsetget)
流程修改设备 IP，并同步修改 env、路由和本文映射。

## 3. 部署前检查

在仓库根目录记录版本，并确认只有计划内改动：

```bash
git status --short --branch
git rev-parse HEAD
```

在 Unitree 上做只读盘点。不要先停止或覆盖不明服务：

```bash
ssh unitree@192.168.123.164
uname -a
cat /etc/os-release
ip -br address show dev eth0
ip route show table main
sudo systemctl status docker --no-pager
sudo systemctl list-unit-files 'wuji-hand2*'
sudo ss -lntp '( sport = :8765 or sport = :8767 )'
sudo docker image inspect codex/xrobotoolkit-runtime:jammy --format '{{.Id}}'
```

部署依赖目标机已有 `codex/xrobotoolkit-runtime:jammy`。宿主是 Ubuntu 20.04、
glibc 2.31，而 `wuji-sdk 2026.8.3` 需要更新的 glibc，因此不要把 SDK 直接装进
宿主 Python；服务使用 Ubuntu 22.04 容器隔离运行。

如目标机下载慢，先在开发机准备 wheel 和模型，再一次性传输；不要让
`docker build` 临时访问外网。

## 4. 准备 ARM64 离线镜像

### 4.1 在开发机建立构建上下文

以下命令从仓库根目录执行。`pip download` 可按现场网络配置 PyPI 镜像源；下载
完成后构建阶段固定使用 `--network none`。

```bash
CTX="$(mktemp -d /tmp/glove-hand2-build.XXXXXX)"
mkdir -p "$CTX/wheelhouse" "$CTX/app"
cp deploy/unitree_hand2/Dockerfile "$CTX/Dockerfile"

python3 -m pip download \
  --no-deps --only-binary=:all: \
  --implementation cp --python-version 310 --abi cp310 \
  --platform manylinux_2_34_aarch64 \
  --platform manylinux_2_28_aarch64 \
  --platform manylinux_2_27_aarch64 \
  --platform manylinux_2_26_aarch64 \
  --platform manylinux_2_24_aarch64 \
  --platform manylinux_2_17_aarch64 \
  --platform manylinux2014_aarch64 \
  --platform any \
  -r deploy/unitree_hand2/requirements-runtime-arm64.txt \
  -d "$CTX/wheelhouse"

rsync -a \
  --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' \
  ./ "$CTX/app/"
bash deploy/unitree_hand2/prepare_model_assets.sh "$CTX/model-assets"

find "$CTX/wheelhouse" -maxdepth 1 -name '*.whl' -type f | wc -l
sha256sum "$CTX"/wheelhouse/*.whl > "$CTX/WHEELHOUSE_SHA256SUMS"
test "$(find "$CTX/wheelhouse" -maxdepth 1 -name '*.whl' -type f | wc -l)" -eq 33
test "$(cat "$CTX/model-assets/WUJI_DESCRIPTION_COMMIT")" = \
  7d547ad50ca8cff92d999ae2cc01fc69bcb7c2b6
```

模型脚本只取官方 `wuji-description` 中 Hand2 的 URDF、MJCF 和 mesh，并锁定到
commit `7d547ad50ca8cff92d999ae2cc01fc69bcb7c2b6`。

### 4.2 传输并在 Unitree 构建

```bash
REMOTE_CTX=/home/unitree/hand2-build
ssh unitree@192.168.123.164 "mkdir -p '$REMOTE_CTX'"
rsync -a --delete "$CTX/" unitree@192.168.123.164:"$REMOTE_CTX/"

ssh unitree@192.168.123.164
cd /home/unitree/hand2-build
sha256sum -c WHEELHOUSE_SHA256SUMS
sudo docker build --network none \
  -t codex/glove-to-hand-hand2:7abfcdb .
sudo docker run --rm --network none \
  codex/glove-to-hand-hand2:7abfcdb \
  -c 'import mujoco,nlopt,numpy,pinocchio,scipy,wuji_sdk,zmq; print("imports_ok")'
```

镜像标签中的 `7abfcdb` 是本次部署的代码基线。若部署后续提交，应使用新的明确
标签，同时更新 `HAND2_IMAGE`，不要复用可变的 `latest`。

## 5. 网络与只读 Hand2 验收

先临时添加与 systemd 单元相同的网络配置：

```bash
sudo ip address replace 192.168.1.100/32 dev eth0
sudo ip route replace 192.168.1.110/32 dev eth0 src 192.168.1.100
sudo ip route replace 192.168.1.111/32 dev eth0 src 192.168.1.100
ip route get 192.168.1.110
ip route get 192.168.1.111
ping -I 192.168.1.100 -c 20 192.168.1.110
ping -I 192.168.1.100 -c 20 192.168.1.111
```

在镜像中扫描；这一步只发现设备，不使能：

```bash
sudo docker run --rm --network host --user 1000:1000 --env HOME=/tmp \
  codex/glove-to-hand-hand2:7abfcdb \
  -c 'from wuji_sdk import SdkManager; print([(str(d.sn),str(d.address)) for d in SdkManager.instance().scan()])'
```

对左右手分别执行只读诊断，命令中不要加入 `--enable-motion`：

```bash
sudo docker run --rm --network host --user 1000:1000 --env HOME=/tmp \
  codex/glove-to-hand-hand2:7abfcdb wuji_hand_test.py \
  --hand left --hand-sn WH2JA01260717002

sudo docker run --rm --network host --user 1000:1000 --env HOME=/tmp \
  codex/glove-to-hand-hand2:7abfcdb wuji_hand_test.py \
  --hand right --hand-sn WH2KA01260730030
```

通过门槛：每侧序列号和 handedness 正确、`online=20/20`、诊断帧 20 个关节、
当前错误为空，最后打印 `Read-only check complete; Hand 2 was not enabled.`。
任何一项不满足都不要安装/启动命令服务。

## 6. 安装并启动 systemd 服务

先人工复核 `wuji-hand2.env` 中的 SN、地址、镜像、限流和安全参数。当前参数为：
`200 Hz`、平滑 `0.02 s`、最大关节速度 `6 rad/s`、看门狗 `1 s`、
`KP=3.5`、`KD=0.1`、电流限制 `1.5 A`。

安装脚本会复制精确文件并 `enable` 三个单元，但不会自行启动：

```bash
cd /home/unitree/hand2-build/app
sudo bash deploy/unitree_hand2/install_services.sh \
  deploy/unitree_hand2/wuji-hand2.env
sudo systemd-analyze verify \
  /etc/systemd/system/wuji-hand2-network.service \
  /etc/systemd/system/wuji-hand2@.service
sudo systemctl start wuji-hand2-network.service \
  wuji-hand2@left.service wuji-hand2@right.service
```

## 7. 服务级验收（不运动）

### 7.1 systemd、容器、端口与路由

```bash
sudo systemctl is-enabled wuji-hand2-network.service \
  wuji-hand2@left.service wuji-hand2@right.service
sudo systemctl is-active wuji-hand2-network.service \
  wuji-hand2@left.service wuji-hand2@right.service
sudo systemctl show wuji-hand2@left.service wuji-hand2@right.service \
  -p MainPID -p NRestarts -p ActiveEnterTimestamp
sudo docker ps --filter name=wuji-hand2 --format '{{.Names}} {{.Status}} {{.Image}}'
sudo ss -lntp '( sport = :8765 or sport = :8767 )'
ip address show dev eth0
ip route show 192.168.1.110/32
ip route show 192.168.1.111/32
```

通过门槛：三个单元均 `enabled`/`active`；左右容器各一个；8765、8767 各只有
一个监听者；`NRestarts=0`；两条路由均以 `src 192.168.1.100` 直连 `eth0`。

### 7.2 hello-only 安全探针

从 RDK 或可访问 `.164` 的主机只发送 hello，不发送 qpos。可使用仓库协议函数：

```bash
python3 - <<'PY'
import socket, time
from qpos_protocol import encode_message, make_hello_message
for side, port in (("left", 8765), ("right", 8767)):
    with socket.create_connection(("192.168.123.164", port), timeout=3) as sock:
        sock.sendall(encode_message(make_hello_message(side, time.time())))
PY
```

随后检查日志和只读诊断：

```bash
sudo journalctl -u wuji-hand2@left.service -u wuji-hand2@right.service \
  --since '-5 min' --no-pager
# 再执行第 5 节两条不带 --enable-motion 的 wuji_hand_test.py。
```

日志应显示 Hand2 保持 disabled；诊断应显示 20 个关节均为 Ready，不能出现
Enabled。探针结束后两个服务应重新回到监听状态，且 `NRestarts=0`。

### 7.3 容器内聚焦测试

```bash
sudo docker run --rm --network none \
  --entrypoint python3 codex/glove-to-hand-hand2:7abfcdb \
  -m unittest -v \
  tests.test_hand2_adaptation \
  tests.test_data_collector_telemetry \
  tests.test_hand_qpos_server
```

本次结果为 `Ran 29 tests ... OK`。这不包含 MCAP 回放全套测试，也不能替代
真机运动验收。

### 7.4 DataCollector 可达性

```bash
ping -c 3 192.168.123.222
for port in 6013 6014 6015 6016; do
  timeout 2 bash -c "</dev/tcp/192.168.123.222/$port" \
    && echo "$port open" || echo "$port unavailable"
done
```

遥测发送是非阻塞 best-effort，collector 不可达不会阻塞手控制。本次 `.222`
不可达且端口未开放，所以不能宣称数采已验收。正式采集时应先启动
DataCollector，再检查对应流消息数持续增加且 `malformed_count=0`。

## 8. 首次真机运动验收（本次未执行）

只有负责人批准后执行：退出 Wuji Studio，清空双手周边，确认左右手映射，安排
急停/断电人员，然后在 RDK 仓库目录运行：

```bash
HOST_RETARGET_HOST=192.168.123.164 \
LEFT_PORT=8765 RIGHT_PORT=8767 \
DATA_COLLECTOR_TELEMETRY=0 \
bash teleop_dual_rdk_keypoints.sh
```

建议先保持一只手完全静止，只缓慢弯曲另一只手的一个关节，分别确认左右选择、
关节顺序、方向、幅度和平滑限制，再交换侧。验收至少包括：

1. 左手输入只驱动左 Hand2，右手输入只驱动右 Hand2。
2. 20 关节顺序与期望一致，无突跳、发散或持续撞限位。
3. 停止 RDK 进程后 `1 s` 内手进入 disabled，服务仍可监听下一客户端。
4. 重新连接前 Hand2 保持 disabled，不因 hello-only 或服务重启而自动使能。
5. 若启用数采，6013-6016 流持续增加且无 malformed。

任何异常立即停 RDK；若看门狗未按预期禁用，则直接断电，不要靠继续发命令恢复。

## 9. 日常启动、查看与停机

```bash
# 查看
sudo systemctl status wuji-hand2-network.service \
  wuji-hand2@left.service wuji-hand2@right.service --no-pager
sudo journalctl -fu wuji-hand2@left.service
sudo journalctl -fu wuji-hand2@right.service

# 启动
sudo systemctl start wuji-hand2-network.service \
  wuji-hand2@left.service wuji-hand2@right.service

# 停机：先在 RDK 上 Ctrl-C，并等待至少 1 s，再执行
sudo systemctl stop wuji-hand2@left.service wuji-hand2@right.service

# 若不再需要 Hand2 专用地址和路由
sudo systemctl stop wuji-hand2-network.service
```

## 10. 故障处理与回滚

先保留证据：

```bash
sudo systemctl status wuji-hand2-network.service \
  wuji-hand2@left.service wuji-hand2@right.service --no-pager
sudo journalctl -u wuji-hand2-network.service \
  -u wuji-hand2@left.service -u wuji-hand2@right.service \
  --since '-30 min' --no-pager
ip -details address show dev eth0
ip route show table main
sudo docker ps -a --filter name=wuji-hand2
```

本次部署前没有同名 Hand2 单元；安装证据和部署文件备份位于
`/var/backups/wuji-hand2/install_20260812_211314`。需要撤销本次新增服务时，
先停 RDK，再执行精确目标的回滚：

```bash
sudo systemctl disable --now wuji-hand2@left.service wuji-hand2@right.service
sudo systemctl disable --now wuji-hand2-network.service
sudo rm /etc/systemd/system/wuji-hand2@.service
sudo rm /etc/systemd/system/wuji-hand2-network.service
sudo rm /etc/default/wuji-hand2
sudo rm /opt/glove_to_hand/deploy/unitree_hand2/run_hand2_container.sh
sudo systemctl daemon-reload
sudo systemctl reset-failed
```

网络单元停止时会只删除 `.110`、`.111` 两条 `/32` 路由和
`192.168.1.100/32` 地址，不会删除原有经 `192.168.123.233` 的 `/24` 路由。
镜像和构建目录先保留用于复盘，确认不再需要后再单独清理。

## 11. 2026-08-12 实测记录

- 目标：Ubuntu 20.04 ARM64，kernel `5.10.104-tegra`；boot ID
  `6d7aae41-49cb-4a82-9c71-eb2080575366`。
- 镜像：`codex/glove-to-hand-hand2:7abfcdb`，约 `1.02 GB`；目标构建目录
  `/home/unitree/hand2-build.VQHyht`。
- 左手：固件 `2.2.3`，`20/20` online，无当前错误，温度
  `52.1..60.1 C`，母线电压 `12.04..12.15 V`。
- 右手：固件 `2.2.3`，`20/20` online，无当前错误，温度
  `53.2..61.5 C`，母线电压 `12.00..12.20 V`。
- 每只手 20 次 ping 均 `0%` 丢包；平均 RTT 左约 `0.514 ms`、右约
  `0.370 ms`。
- 三个 systemd 单元已 enabled/active；左右服务 `NRestarts=0`，8765/8767
  正常监听；hello-only 后两侧均为 `Ready 20/20`，没有关节进入 Enabled。
- 尚未重启目标机验证开机自启；尚未进行 RDK 真动作和 DataCollector 实流验收。
