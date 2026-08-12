# 串口连接开发板
```
  sudo apt install picocom
  sudo picocom -b 115200 /dev/ttyUSB0
  输入用户名和密码都为sunrise
  ip -4 addr show wlan0
```

# Glove 连接 RDK_X5

```
Host sunrise
    HostName 192.168.112.230
    User sunrise
PWD sunrise
```

```
RDK_X5 运行
  sudo ip addr add 192.168.1.20/24 dev eth0
  sudo ip route replace 192.168.1.100/32 dev eth0 src 192.168.1.20
  sudo ip route replace 192.168.1.101/32 dev eth0 src 192.168.1.20
  sudo ip neigh flush to 192.168.1.100
  sudo ip neigh flush to 192.168.1.101

设置端口后尝试Ping手套
ping 192.168.1.100 （左手）
ping 192.168.1.101 （右手）

```

# 环境配置
```
cd wuji-retargeting
pip install -r requirements.txt
pip istall -e .
```

# Wuji Hand2 连接机器人
```
机器人运行
  sudo ip addr add 192.168.1.200/24 dev enx6c1ff7d6f986
  sudo ip route replace 192.168.1.110/32 dev enx6c1ff7d6f986 src 192.168.1.200
  sudo ip route replace 192.168.1.111/32 dev enx6c1ff7d6f986 src 192.168.1.200
  sudo ip neigh flush to 192.168.1.110
  sudo ip neigh flush to 192.168.1.111

设置端口后尝试 Ping Hand2
ping 192.168.1.110 （左手）
ping 192.168.1.111 （右手）

```
注意上述`enx6c1ff7d6f986`需要替换为机器人连接的对应网口

# retargeting运行逻辑
rdk_x5采集手套keypoint发送给主机做retargeting，再发送给机器人端执行

# 机器人端运行
```
cd glove_to_hand/
bash teleop_dual_robot_hand.sh
```

# 本地运行
```
cd glove_to_hand/
ROBOT_HAND_HOST=robot.ip bash teleop_dual_host_bridge.sh
```

# RDK_X5运行
```
conda activate wuji_new
bash /home/sunrise/glove_to_hand/teleop_dual_rdk_keypoints.sh
```
