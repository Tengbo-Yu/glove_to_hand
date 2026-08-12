#!/usr/bin/env bash
set -euo pipefail

output_dir="${1:?Usage: $0 OUTPUT_DIR}"
commit="${WUJI_DESCRIPTION_COMMIT:-7d547ad50ca8cff92d999ae2cc01fc69bcb7c2b6}"
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/wuji-description.XXXXXX")"
cleanup() {
  rm -rf -- "$temp_dir"
}
trap cleanup EXIT

archive="$temp_dir/wuji-description.tar.gz"
curl -fL --retry 3 --connect-timeout 10 \
  "https://codeload.github.com/wuji-technology/wuji-description/tar.gz/$commit" \
  -o "$archive"
tar -C "$temp_dir" -xzf "$archive"
source_dir="$temp_dir/wuji-description-$commit"

if [[ ! -f "$source_dir/hand2/body/urdf/left.urdf" \
   || ! -f "$source_dir/hand2/body/urdf/right.urdf" \
   || ! -f "$source_dir/hand2/body/mjcf/left.xml" \
   || ! -f "$source_dir/hand2/body/mjcf/right.xml" \
   || ! -f "$source_dir/hand2/body/meshes/left/l_wrist.STL" \
   || ! -f "$source_dir/hand2/body/meshes/right/r_wrist.STL" ]]; then
  echo "Wuji Hand2 model assets are incomplete at commit $commit" >&2
  exit 1
fi

install -d -m 0755 "$output_dir/hand2/body"
cp -a "$source_dir/hand2/body/urdf" "$output_dir/hand2/body/"
cp -a "$source_dir/hand2/body/mjcf" "$output_dir/hand2/body/"
cp -a "$source_dir/hand2/body/meshes" "$output_dir/hand2/body/"
printf '%s\n' "$commit" > "$output_dir/WUJI_DESCRIPTION_COMMIT"
