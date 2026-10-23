#!/usr/bin/env bash
# Loaded once through ~/.bashrc. Compatible with older omx_start aliases.
[[ $- == *i* ]] || return 0
[[ -f "$HOME/.config/logistics_omx/device.env" ]] || return 0
source "$HOME/.config/logistics_omx/device.env"
unalias omx_start 2>/dev/null || true
omx_start() {
  "$HOME/Logistics_OMX/run_omx_mqtt.sh" "$@"
}
if [[ "$OMX_ROBOT_ID" == omx1 || "$OMX_ROBOT_ID" == omx2 ]]; then
  omx_reset() {
    local file="${OMX_INVENTORY_STATE_PATH:-$HOME/Logistics_OMX/state/omx_inventory.json}"
    if [[ ! -f "$file" ]]; then echo "상태 파일이 없습니다: $file"; return 1; fi
    if pgrep -af '[l]erobot-rollout|[o]mx_mqtt_client.py' >/dev/null 2>&1; then
      echo '[ERROR] OMX 실행 중에는 주문/재고 초기화를 허용하지 않습니다.' >&2
      return 1
    fi
    echo '주의: 기존 주문 및 재고 카운터를 초기화합니다.'
    local answer
    read -r -p '실제 물품을 다시 채웠습니까? (yes): ' answer
    [[ "$answer" == yes ]] || return 0
    cp -a "$file" "$file.bak.$(date +%Y%m%d_%H%M%S)"
    OMX_RESET_FILE="$file" python3 - <<'__OMX_RESET_PY__'
import json
import os
import time
from pathlib import Path
path = Path(os.environ['OMX_RESET_FILE']).expanduser()
data = json.loads(path.read_text(encoding='utf-8'))
now = time.strftime('%Y-%m-%dT%H:%M:%S%z')
data.update({
    'total_completed': 0, 'remaining': 0,
    'order_item': 'WAIT', 'order_quantity': 0,
    'order_completed': 0, 'order_started_at': now,
    'order_started_at_epoch': time.time(), 'updated_at': now,
})
temporary = path.with_suffix(path.suffix + '.tmp')
temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
os.replace(temporary, path)
print(f"초기화 완료: capacity={data.get('capacity')}, total_completed=0, remaining=0, WAIT")
__OMX_RESET_PY__
  }
else
  # OMX3/4에는 재고 초기화 기능이 없어야 한다.
  unset -f omx_reset 2>/dev/null || true
fi
