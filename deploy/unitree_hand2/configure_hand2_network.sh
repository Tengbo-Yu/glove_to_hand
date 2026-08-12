#!/usr/bin/env bash
set -euo pipefail

readonly IFACE="${HAND2_INTERFACE:-eth0}"
readonly MANAGEMENT_CIDR="${HAND2_MANAGEMENT_CIDR:-192.168.123.164/24}"
readonly MANAGEMENT_WAIT_SECONDS="${HAND2_MANAGEMENT_WAIT_SECONDS:-60}"
readonly HAND_HOST_CIDR="${HAND2_HOST_CIDR:-192.168.1.100/32}"
readonly HAND_HOST_IP="${HAND_HOST_CIDR%/*}"
readonly LEFT_HAND_IP="${LEFT_HAND_IP:-192.168.1.110}"
readonly RIGHT_HAND_IP="${RIGHT_HAND_IP:-192.168.1.111}"

has_address() {
  local cidr="$1"
  /usr/sbin/ip -4 -o address show dev "$IFACE" | /usr/bin/grep -Fq " inet ${cidr} "
}

start_network() {
  /usr/sbin/ip link show dev "$IFACE" >/dev/null

  if [[ ! "$MANAGEMENT_WAIT_SECONDS" =~ ^[0-9]+$ ]]; then
    echo "HAND2_MANAGEMENT_WAIT_SECONDS must be a non-negative integer" >&2
    exit 2
  fi

  # The Unitree network stack can publish network-online.target shortly before
  # the static management address appears. Wait for that exact address, but
  # never replace or otherwise mutate it here.
  if ! has_address "$MANAGEMENT_CIDR"; then
    echo "Waiting up to ${MANAGEMENT_WAIT_SECONDS}s for ${MANAGEMENT_CIDR} on ${IFACE}"
    for ((second = 0; second < MANAGEMENT_WAIT_SECONDS; second++)); do
      /usr/bin/sleep 1
      if has_address "$MANAGEMENT_CIDR"; then
        break
      fi
    done
  fi

  # Fail closed after the bounded wait: hand services stay down while the
  # robot's management network remains untouched and recoverable.
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
