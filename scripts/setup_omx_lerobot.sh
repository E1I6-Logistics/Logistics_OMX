#!/usr/bin/env bash
set -Eeuo pipefail
: "${VIRTUAL_ENV:?먼저 Python 가상환경을 활성화하세요.}"
BUNDLE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${LEROBOT_DEST:-$HOME/il_ws/src/lerobot_omx_release}"
if [[ -e "$DEST" ]]; then echo "기존 폴더 보존: $DEST (새 LEROBOT_DEST를 지정하세요)" >&2; exit 1; fi
python -c 'import sys; assert sys.version_info >= (3,12); assert sys.prefix != sys.base_prefix'
git clone https://github.com/huggingface/lerobot.git "$DEST"
git -C "$DEST" checkout --detach 89236ea0f4f81a81ca566081e20dd1ff5f823cbe
cp -a "$BUNDLE/robots_ws/src/omx_manipulator/overlay/." "$DEST/"
cp -a "$BUNDLE/robots_ws/src/omx_manipulator/runtime/." "$DEST/"
python -m pip install -e "$DEST[dynamixel,intelrealsense]"
python -m pip check
chmod +x "$DEST/run_ab_orders.sh" "$DEST/omx_new_order.sh" "$DEST/omx_order_console.sh"
echo "설치 완료: $DEST — 모델 폴더 및 캘리브레이션은 별도로 준비하세요."
