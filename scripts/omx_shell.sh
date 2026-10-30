#!/usr/bin/env bash
# .bashrc에서 불러오는 단축 명령. 불러오기만 할 때 장치/서비스를 실행하지 않습니다.
_OMX_SHELL_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
_OMX_SHELL_REGISTRY="$HOME/.config/logistics_omx/shell.env"
if [[ -f "$_OMX_SHELL_REGISTRY" ]]; then
  source "$_OMX_SHELL_REGISTRY"
elif [[ -f "$HOME/.config/logistics_omx/device.env" ]]; then
  source "$HOME/.config/logistics_omx/device.env"
  OMX_CONFIG_PATH="$_OMX_SHELL_ROOT/state/config/${OMX_ROBOT_ID:-}.yaml"
  OMX_PYTHON="${OMX_PYTHON:-${OMX_PYTHON_ENV_PATH:-$HOME/venv/.venv}/bin/python}"
else
  return 0
fi
export OMX_ROBOT_ID OMX_CONFIG_PATH OMX_PYTHON
# 이전 alias와 재고 직접 초기화 함수를 제거하고 현재 YAML 관리 명령을 사용합니다.
for _omx_name in omx_start omx_home omx_arm omx_release omx_observe omx_capture omx_check omx_calibrate omx_logs omx_monitor omx_status omx_recover omx_reset omx_order omx_service omx_info omx_help omx_cd; do
  unalias "$_omx_name" 2>/dev/null || true
done
unset -f omx_reset 2>/dev/null || true
unset _omx_name
_omx_ctl() {
  if [[ ! -f "${OMX_CONFIG_PATH:-}" ]]; then
    echo '[ERROR] 현장 YAML이 없습니다. bash scripts/install_omx_shell.sh 로 등록하세요.' >&2
    return 2
  fi
  "$_OMX_SHELL_ROOT/omx_control" --config "$OMX_CONFIG_PATH" --expected-device "$OMX_ROBOT_ID" "$@"
}
omx_start() {
  echo "[OMX] $OMX_ROBOT_ID 실행: $OMX_CONFIG_PATH"
  "$_OMX_SHELL_ROOT/run_omx_mqtt.sh" --robot-id "$OMX_ROBOT_ID" --config "$OMX_CONFIG_PATH" --confirm-motion --important-only "$@"
}
omx_home() { _omx_ctl home --confirm-motion "$@"; }
omx_arm() { _omx_ctl arm --confirm-motion "$@"; }
omx_release() { _omx_ctl release --confirm-motion "$@"; }
omx_observe() { _omx_ctl observe "$@"; }
omx_capture() { _omx_ctl capture "$@"; }
omx_check() { _omx_ctl check "$@"; }
omx_calibrate() { _omx_ctl menu "$@"; }
omx_logs() { _omx_ctl logs "$@"; }
omx_monitor() { _omx_ctl monitor "$@"; }
omx_status() { _omx_ctl monitor --samples 1 "$@"; }
omx_recover() { _omx_ctl acknowledge-recovery --confirm-motion "$@"; }
omx_order() {
  if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo '사용법: omx_order 품목 [수량=1] — 실제 팔이 움직입니다.' >&2
    return 2
  fi
  _omx_ctl order --item "$1" --quantity "${2:-1}"
}
omx_service() {
  local action="${1:-status}"
  if [[ $# -gt 0 ]]; then shift; fi
  case "$action" in
    start|enable) _omx_ctl service --action "$action" --confirm-motion "$@";;
    *) _omx_ctl service --action "$action" "$@";;
  esac
}
omx_cd() { cd -- "$_OMX_SHELL_ROOT"; }
omx_info() {
  printf 'OMX: %s\nYAML: %s\nPython: %s\nRepo: %s\n' "$OMX_ROBOT_ID" "$OMX_CONFIG_PATH" "$OMX_PYTHON" "$_OMX_SHELL_ROOT"
}
omx_help() {
  omx_info
  cat <<'HELP'
omx_start [--diagnostics]   실행 / 전경 실행 종료는 Ctrl+C
omx_home                    등록 홈 근처에서 제한 복귀 (실제 이동)
omx_arm                     현재 자세 유지 토크 ON (홈 접근 범위 안)
omx_release                 YAML 대기 시간 후 전체 토크 OFF — 팔 지지 필수
omx_observe                 관절값 확인
omx_capture                 현재 자세를 홈으로 저장 — 복귀 명령이 아님
omx_check                   파일·등록 홈 검사
omx_calibrate               설정/보정 메뉴
omx_status                  현재 상태 한 번 조회
omx_monitor                 성능 모니터 (Ctrl+C로 모니터만 종료)
omx_logs [--follow]          중요 로그 조회
omx_recover                 물품 확인 후 중단 작업 기록 보관/해제
omx_order A 1               수동 MQTT 실물 주문 (장비에 맞는 품목 사용)
omx_service install         자동 재시작 서비스 설치만 수행
omx_service start|stop|status 서비스 구동/수동 정지/상태
omx_cd                      저장소로 이동
HELP
}
