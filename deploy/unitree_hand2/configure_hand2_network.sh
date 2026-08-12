#!/usr/bin/env bash
set -euo pipefail

readonly IFACE="${HAND2_INTERFACE:-eth0}"
readonly MANAGEMENT_CIDR="${HAND2_MANAGEMENT_CIDR:-192.168.123.164/24}"
readonly HAND_HOST_CIDR="${HAND2_HOST_CIDR:-192.168.1.100/32}"
readonly HAND_HOST_IP="${HAND_HOST_CIDR%/*}"
readonly LEFT_HAND_IP="${LEFT_HAND_IP:-192.168.1.110}"
readonly RIGHT_HAND_IP="${RIGHT_HAND_IP:-192.168.1.111}"

has_address() {
  local cidr="$1"
  /usr/sbin/ip -4 -o address show dev "$IFACE" | /usr/bin/grep -Fq " inet ${cidr} "
}

start_network() {
  /usr/bin/test -e "/sys/class/net/${IFACE}"

  # Fail closed: the management address must already be configured by the
  # robot's normal network stack. Never replace or otherwise mutate it here.
  if ! has_address "$MANAGEMENT_CIDR"; then
    echo "Refusing Hand2 network setup: ${MANAGEMENT_CIDR} is absent on ${IFACE}" >&2
    exit 1
  fi

  if ! has_address "$HAND_HOST_CIDR"; then
    /usr/sbin/ip address add "$HAND_HOST_CIDR" dev "$IFACE"
  fi

  /usr/sbin/ip route replace "${LEFT_HAND_IP}/32" dev "$IFACE" src "$HAND_HOST_IP"
  /usr/sbin/ip route replace "${RIGHT_HAND_IP}/32" dev "$IFACE" src "$HAND_HOST_IP"
}

stop_network() {
  /usr/sbin/ip route del "${RIGHT_HAND_IP}/32" dev "$IFACE" 2>/dev/null || true
  /usr/sbin/ip route del "${LEFT_HAND_IP}/32" dev "$IFACE" 2>/dev/null || true
  /usr/sbin/ip address del "$HAND_HOST_CIDR" dev "$IFACE" 2>/dev/null || true
}

case "${1:-}" in
  start)
    start_network
    ;;
  stop)
    stop_network
    ;;
  *)
    echo "Usage: $0 start|stop" >&2
    exit 2
    ;;
esac
