#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="${1:-$script_dir/wuji-hand2.env}"

if [[ ! -f "$env_file" ]]; then
  echo "Missing deployment environment file: $env_file" >&2
  echo "Copy wuji-hand2.env.example, fill the discovered serial numbers, and set ENABLE_HAND2=1." >&2
  exit 2
fi

image="$(sed -n 's/^HAND2_IMAGE=//p' "$env_file" | tail -1)"
if [[ -z "$image" ]] || ! sudo docker image inspect "$image" >/dev/null 2>&1; then
  echo "Required Docker image is absent: ${image:-unset}" >&2
  exit 2
fi

sudo install -d -m 0755 /opt/glove_to_hand/deploy/unitree_hand2
sudo install -m 0755 "$script_dir/run_hand2_container.sh" \
  /opt/glove_to_hand/deploy/unitree_hand2/run_hand2_container.sh
sudo install -m 0644 "$env_file" /etc/default/wuji-hand2
sudo install -m 0644 "$script_dir/wuji-hand2-network.service" \
  /etc/systemd/system/wuji-hand2-network.service
sudo install -m 0644 "$script_dir/wuji-hand2@.service" \
  /etc/systemd/system/wuji-hand2@.service
sudo systemctl daemon-reload
sudo systemctl enable wuji-hand2-network.service wuji-hand2@left.service wuji-hand2@right.service

echo "Installed and enabled. Start only after read-only Hand2 diagnostics pass:"
echo "  sudo systemctl start wuji-hand2-network.service wuji-hand2@left.service wuji-hand2@right.service"
