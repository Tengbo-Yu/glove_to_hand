#!/usr/bin/env bash
set -euo pipefail

# Configure the dedicated wired teleop subnet without changing the default route.
# Usage:
#   bash setup_wired_network.sh local [iface]
#   bash setup_wired_network.sh host [iface]

ROLE="${1:-local}"
IFACE_ARG="${2:-}"

LOCAL_IFACE="${LOCAL_IFACE:-enx00e04c584b78}"
HOST_IFACE="${HOST_IFACE:-${ROBOT_IFACE:-enx6c1ff7158ced}}"
LOCAL_IP="${LOCAL_WIRED_IP:-192.168.126.10/24}"
HOST_IP="${HOST_WIRED_IP:-${ROBOT_WIRED_IP:-192.168.126.20/24}}"

usage() {
  echo "Usage: $0 {local|host} [iface]" >&2
  echo "  local default: iface=$LOCAL_IFACE ip=$LOCAL_IP peer=${HOST_IP%/*}" >&2
  echo "  host  default: iface=$HOST_IFACE ip=$HOST_IP peer=${LOCAL_IP%/*}" >&2
}

case "$ROLE" in
  local|glove|rdk)
    IFACE="${IFACE_ARG:-$LOCAL_IFACE}"
    IP_CIDR="$LOCAL_IP"
    PEER_IP="${HOST_IP%/*}"
    ;;
  host|robot|server)
    IFACE="${IFACE_ARG:-$HOST_IFACE}"
    IP_CIDR="$HOST_IP"
    PEER_IP="${LOCAL_IP%/*}"
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    usage
    exit 1
    ;;
esac

if [[ ! -e "/sys/class/net/$IFACE" ]]; then
  echo "ERROR: interface '$IFACE' does not exist." >&2
  echo "Available interfaces:" >&2
  ip -brief link >&2
  exit 1
fi

configure_with_nmcli() {
  command -v nmcli >/dev/null 2>&1 || return 1

  local con_name
  con_name="$(nmcli -t -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null | head -n 1 || true)"
  if [[ -z "$con_name" || "$con_name" == "--" ]]; then
    return 1
  fi

  echo "Configuring NetworkManager connection: $con_name"
  sudo nmcli con modify "$con_name" \
    ipv4.method manual \
    ipv4.addresses "$IP_CIDR" \
    ipv4.gateway "" \
    ipv4.never-default yes \
    ipv4.routes "" \
    ipv4.ignore-auto-routes yes \
    ipv4.ignore-auto-dns yes \
    ipv6.method disabled \
    connection.autoconnect yes
  sudo nmcli con up "$con_name"
}

configure_with_ip() {
  echo "Configuring interface directly with ip command."
  sudo ip link set "$IFACE" up

  while read -r old_addr; do
    [[ -n "$old_addr" ]] && sudo ip addr del "$old_addr" dev "$IFACE" || true
  done < <(ip -o -4 addr show dev "$IFACE" | awk '{print $4}' | grep '^192\.168\.126\.' || true)

  if ! ip -o -4 addr show dev "$IFACE" | grep -q " $IP_CIDR "; then
    sudo ip addr add "$IP_CIDR" dev "$IFACE"
  fi
}

if ! configure_with_nmcli; then
  configure_with_ip
fi

sudo ip route flush cache 2>/dev/null || true

echo ""
echo "Configured $ROLE wired teleop network:"
echo "  iface: $IFACE"
echo "  ip:    $IP_CIDR"
echo "  peer:  $PEER_IP"
echo ""
echo "Route to peer:"
ip route get "$PEER_IP" || true

echo ""
echo "Ping test:"
ping -c 2 -W 1 "$PEER_IP" || true
