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
for _omx_name in omx_start omx_home omx_arm omx_release omx_observe omx_capture omx_check omx_calibrate omx_logs omx_monitor omx_status omx_recover omx_reset omx_order omx_service omx_info omx_help omx_cd omx_setup omx_workflow omx_stage omx_route omx_route_test omx_return_home omx_basket omx_resume omx_unload omx_tune omx_cycle_calibrate; do
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
omx_setup() { _omx_ctl setup-workflow "$@"; }
omx_tune() { _omx_ctl tune "$@"; }
omx_cycle_calibrate() {
  if [[ $# -ne 1 ]]; then echo '사용법: omx_cycle_calibrate 품목 — 모델이 실제로 움직입니다.' >&2; return 2; fi
  _omx_ctl cycle-calibrate --item "$1" --confirm-motion
}
omx_workflow() { _omx_ctl workflow-check "$@"; }
omx_stage() {
  if [[ $# -ne 2 ]]; then echo '사용법: omx_stage 품목 단계이름 (예: omx_stage F pick)' >&2; return 2; fi
  _omx_ctl stage-capture --item "$1" --name "$2"
}
omx_route() {
  if [[ $# -ne 1 ]]; then echo '사용법: omx_route 경로이름 — 현 자세를 다음 경유점으로 추가' >&2; return 2; fi
  _omx_ctl route-capture --name "$1"
}
omx_route_test() {
  if [[ $# -ne 1 ]]; then echo '사용법: omx_route_test 경로이름 — 홈에서 경로 왕복 실제 이동' >&2; return 2; fi
  _omx_ctl route-test --name "$1" --confirm-motion --round-trip
}
omx_return_home() { _omx_ctl return-home --confirm-motion "$@"; }
omx_basket() {
  case "${1:-check}" in
    snapshot) _omx_ctl basket-snapshot;;
    capture) _omx_ctl basket-capture --confirm-empty;;
    check) _omx_ctl basket-check;;
    watch) _omx_ctl basket-watch;;
    status) _omx_ctl basket-status;;
    roi|polygon)
      if [[ $# -ne 2 ]]; then echo '사용법: omx_basket roi "[x,y,w,h]" / omx_basket polygon "[[x,y],...]" (해제: null)' >&2; return 2; fi
      local key=unload.roi
      if [[ "$1" == polygon ]]; then key=unload.roi_polygon; fi
      _omx_ctl set --key "$key" --value "$2";;
    confirm-view)
      if [[ $# -ne 1 ]]; then echo '사용법: omx_basket confirm-view — 바구니 내부 전체와 정차 오차를 직접 확인한 경우에만 실행' >&2; return 2; fi
      _omx_ctl set --key unload.full_view_confirmed --value true;;
    *) echo '사용법: omx_basket snapshot|capture|check|watch|status|roi|polygon|confirm-view — 전체 내부 시야 확인 후 confirm-view, 빈 상태에서만 capture' >&2; return 2;;
  esac
}
omx_resume() { _omx_ctl resume --confirm-motion "$@"; }
omx_unload() {
  local item
  case "$OMX_ROBOT_ID" in omx3) item=E;; omx4) item=F;; *) echo 'OMX3/4 하차 전용 명령입니다.' >&2; return 2;; esac
  if [[ $# -eq 0 ]]; then _omx_ctl order --until-empty
  elif [[ $# -eq 1 ]]; then _omx_ctl order --item "$item" --quantity "$1"
  else echo '사용법: omx_unload [예상 수량] — 생략하면 빈 바구니까지 제한 반복' >&2; return 2; fi
}
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
omx_setup                   기존 설정에 작업 단계·반복 하차·복귀 경로 설정 추가
omx_tune                    CPU 스레드 4·사이클 카운터·바구니 검사 기본 0.1초/3프레임 적용
omx_cycle_calibrate A       모델을 돌리며 정상 복귀 3회를 확인·학습 (실제 이동)
omx_workflow                모델 복귀 프로필·하차 기준 영상 등록 검사
omx_stage F pick            이전 수동 단계 등록 (cycle 모드에서는 사용하지 않음)
omx_route fold              현재 자세를 fold 복귀 경로의 다음 지점으로 등록
omx_route_test fold         홈에서 첫 경유점까지 역방향 이동 후 복귀 시험·승인
omx_return_home             홈 접근 또는 시험된 경로를 이용한 실제 복귀
omx_basket snapshot         홈에서 상단 영상 좌표 격자 PNG 저장
omx_basket capture          빈 바구니 직접 확인 후 기준 저장
omx_basket check            빈 바구니/물건 있음/불명확 판정 시험
omx_basket watch            카메라 한 번 연결 후 실시간 판정 (Ctrl+C로 종료)
omx_basket status           실행 중 컨트롤러의 최신 영상 판정 조회 (장치 연결 없음)
omx_basket roi '[x,y,w,h]'   검사 사각형 설정 (전체 시야 확인 해제, 기준 재등록 필요)
omx_basket polygon '[[x,y],...]' 바구니 내부 다각형 설정 (null로 해제)
omx_basket confirm-view     내부 전체·정차 오차·모서리 검출을 직접 확인한 뒤에만 사용
omx_resume                  물품 확인 후 실행 중인 복구 대기에 재점검 요청 (작업 재실행 안 함)
omx_unload [수량]            OMX3/4 하차: 수량 생략 시 빈 바구니까지, 지정 시 수량도 대조
omx_order A 1               수동 MQTT 실물 주문 (장비에 맞는 품목 사용)
omx_service install         자동 재시작 서비스 설치만 수행
omx_service start|stop|status 서비스 구동/수동 정지/상태
omx_cd                      저장소로 이동
HELP
}
