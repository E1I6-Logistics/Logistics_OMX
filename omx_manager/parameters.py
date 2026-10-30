"""Korean parameter comments regenerated on every YAML save (no extra dependency)."""
import re
import yaml

DIAGNOSTIC_DEFAULTS = {
    "enabled": False, "important_only": False, "interval_s": 5., "stale_s": 15.,
    "full_log_mb": 2, "important_log_mb": 1, "backups": 1, "queue_lines": 128,
    "slow_warning_interval_s": 10.,
}

SERVICE_DEFAULTS = {"restart_s":5.,"stop_timeout_s":45.,"enable_on_boot":False}

COMMENTS = {
    "schema_version":"설정 파일 형식 버전: 현재 1 사용",
    "device_id":"이 팔의 장비 번호: omx1~omx4",
    "role":"작업 역할: pickup=피킹, unload=하차",
    "hardware":"실제 모터·카메라와 학습 때의 보정 파일 설정",
    "port":"모터 컨트롤러의 고정 장치 경로: 현장 /dev/serial/by-id 값",
    "robot_id":"학습 때 사용한 팔 ID: 보정 JSON 파일명과 일치해야 함",
    "calibration_file":"기존 모터 영점·방향·범위 보정 JSON: 자동 덮어쓰기 안 함",
    "disable_torque_on_disconnect":"종료 시 토크 해제 여부: false=유지, 하중을 고려해 결정",
    "cameras":"모델 config의 영상 입력 이름과 정확히 일치해야 함",
    "type":"카메라 종류: opencv=USB, intelrealsense=RealSense",
    "index_or_path":"영상 장치 경로: 현장에서 실제 카메라 by-id 값 지정",
    "serial_number_or_name":"해당 RealSense의 실제 시리얼 번호",
    "width":"입력 영상 가로 픽셀: 모델 입력 크기와 일치",
    "height":"입력 영상 세로 픽셀: 모델 입력 크기와 일치",
    "processing_fps":"영상 버퍼에 게시할 목표 FPS: 하드웨어 수신 FPS와 별개",
    "fourcc":"USB 영상 전송 포맷: MJPG 등 장치가 지원하는 값",
    "warmup_s":"카메라 연결 후 준비 시간(초)",
    "use_rgb":"RealSense 컬러 영상 사용 여부",
    "use_depth":"RealSense 깊이 영상 사용 여부: 모델이 요구할 때만 사용",
    "home":"고정 초기 자세 등록 및 복귀 판정: 위치 단위는 도가 아닌 정규화 값",
    "target":"등록한 6개 관절 초기 자세: null이면 미등록, capture로 저장",
    "binding":"모델·보정·카메라 연결 해시: 도구가 저장하므로 수동 수정 금지",
    "tolerance":"사이클 홈 복귀 판정의 관절별 허용 오차(정규화 단위)",
    "hold_s":"피킹 홈 복귀 후 연속 정지 확인 시간(초)",
    "unload_hold_s":"하차 홈 복귀 후 연속 정지 확인 시간(초)",
    "still_delta":"연속 관측 간 정지로 인정할 최대 관절 변화(정규화 단위)",
    "max_sample_gap_s":"연속 관측으로 인정할 최대 공백(초): 넘으면 정지 시간 재측정",
    "capture_s":"홈 등록을 위해 관절을 관측하는 시간(초)",
    "capture_max_span":"홈 등록 중 허용하는 관절 최대 흔들림 폭(정규화 단위)",
    "arrival_tolerance":"고정 초기 자세 이동의 도착 오차(정규화 단위): 사이클 판정과 별개",
    "move_speed":"홈 제한 이동의 최대 속도(정규화 단위/초)",
    "move_timeout_s":"홈 제한 이동의 최대 허용 시간(초)",
    "max_start_error":"홈 접근을 허용할 최대 시작 오차(정규화 단위): 너무 멀면 이동 거절",
    "max_step":"홈 이동 한 번의 최대 목표 변화(정규화 단위)",
    "max_following_error":"홈 이동 중 목표와 실측 위치의 최대 차이: 지연 시 목표 전진 대기, 초과 시 정지(정규화 단위)",
    "counting":"홈 이탈·복귀를 이용한 사이클 카운팅: 물품 성공 검출과 별개",
    "leave_tolerance":"새 사이클을 인정할 홈 이탈 오차: 모든 복귀 허용 오차보다 커야 함",
    "min_cycle_s":"한 사이클의 최소 경과 시간(초): 이탈 관측 자체를 늦추지는 않음",
    "stall_s":"홈 근처에서 진행이 없음을 진단하는 시간(초)",
    "progress_epsilon":"홈 접근 진행으로 인정할 오차 감소량(정규화 단위)",
    "models":"품목별 모델 경로와 실행 프로필 설정",
    "active_profile":"사용할 프로필 이름: 현재 original FP32만 구현",
    "profiles":"모델 교체용 프로필 모음: 모델 변경 뒤 초기 자세 재확인·등록 필요",
    "precision":"모델 정밀도: 현재 실행 가능한 값은 fp32",
    "validated":"후보 모델 검증 표시: true만으로 미구현 INT8 실행이 허용되지는 않음",
    "items":"외부 품목 키별 사용할 모델과 작업 문장",
    "path":"체크포인트의 pretrained_model 폴더: 가중치·config·processor 파일 필요",
    "task":"해당 모델에 전달할 작업 문장: 학습 작업과 맞게 지정",
    "broker_host":"FMS와 공유하는 MQTT 브로커 주소: Pi에서 접근 가능한 IP",
    "broker_port":"MQTT 브로커 포트 번호",
    "cpu_threads":"PyTorch·수치 연산 CPU 스레드 수: Pi에서는 1~4 범위",
    "cycle_timeout_s":"피킹 한 사이클 또는 하차 한 작업의 제한 시간(초)",
    "order_per_unit_s":"피킹 주문 감시에서 물품 1개당 허용 시간(초)",
    "order_load_grace_s":"품목 주문 시작·모델 로딩을 위한 추가 여유 시간(초)",
    "startup_timeout_s":"모델·장치 준비 완료를 기다리는 최대 시간(초)",
    "heartbeat_timeout_s":"제어 프로세스 생존 신호가 없어도 기다리는 시간(초)",
    "shutdown_timeout_s":"자식 프로세스 정상 종료 후 강제 정리까지 기다리는 시간(초)",
    "recovery":"오류 후 감독 프로그램의 재시도 간격: 미완료 물리 작업은 자동 재집기 안 함",
    "retry_initial_s":"오류 후 첫 재시도 대기 시간(초)",
    "retry_max_s":"재시도 간격을 늘릴 때의 최대 대기 시간(초)",
    "service":"사용자 systemd 서비스: 감독 프로그램 종료까지 자동 재시작 관리",
    "restart_s":"감독 프로그램 비정상 종료 뒤 서비스 재시작 대기 시간(초)",
    "stop_timeout_s":"서비스 종료 대기 한도(초): 자식 프로세스 정리 시간을 충분히 확보",
    "enable_on_boot":"설치 시 사용자 세션 자동 시작 등록: true면 --confirm-motion 필요",
    "diagnostics":"선택적 중요 로그·성능 계측: 기본 비활성, 제어·영상 설정과 별개",
    "enabled":"상세 성능 카운터와 파일 로그 기본 활성 여부: false 권장 시작값",
    "important_only":"중요 로그 화면·파일 저장 기본 활성 여부: IDLE 반복 출력 제외",
    "interval_s":"상세 성능 스냅샷 및 모니터의 기본 간격(초): 최소 1초",
    "stale_s":"성능 스냅샷을 오래된 값으로 판단할 시간(초): 간격의 2배 이상",
    "full_log_mb":"전체 로그 한 파일의 최대 크기(MiB): 넘으면 순환 보관",
    "important_log_mb":"중요 로그 한 파일의 최대 크기(MiB)",
    "backups":"현재 파일 외에 보관할 이전 로그 파일 수: 최소 1",
    "queue_lines":"비동기 로그 큐의 최대 줄 수: 가득 차면 제어를 막지 않고 로그 일부 생략",
    "slow_warning_interval_s":"반복 제어 주기 경고를 중요 화면에 표시할 최소 간격(초)",
}
JOINT_NAMES={"shoulder_pan":"어깨 좌우", "shoulder_lift":"어깨 상하", "elbow_flex":"팔꿈치", "wrist_flex":"손목 굽힘", "wrist_roll":"손목 회전", "gripper":"그리퍼"}

def comment(path):
    key=path[-1]
    if key in JOINT_NAMES:
        meaning="홈 목표" if 'target' in path else "홈 복귀 허용 오차"
        return JOINT_NAMES[key]+" 관절의 "+meaning+"(정규화 단위, 각도 아님)"
    if key=='fps':return "카메라 하드웨어 요청 FPS: 실제 수신 FPS는 monitor에서 확인" if 'cameras' in path else "정책 제어 루프의 목표 Hz: 실제 달성 주기는 monitor에서 확인"
    if key=='runtime':return "운용 실행·통신·시간 제한 설정" if len(path)==1 else "모델 실행 백엔드: 현재 lerobot_pytorch만 지원"
    if len(path)>=3 and path[-2]=='cameras':return "카메라 입력 키 '"+key+"': 모델 config 이름과 일치해야 함"
    if len(path)>=3 and path[-2]=='profiles':return "모델 프로필 '"+key+"': 원본 또는 교체 후보 설정"
    if len(path)>=3 and path[-2]=='items':return "품목 '"+key+"'의 모델 설정: OMX1 A/B 반대 매핑은 현장 확인된 값"
    return COMMENTS.get(key,"설정 항목 '"+key+"': 운용 문서와 모델·장치 사양 확인")

def annotated_yaml(cfg):
    body=yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False)
    result=['# OMX 팔로워 전용 설정: 위치값은 정규화 단위이며 각도가 아닙니다.',
            '# SET_ 항목은 현장 장치로 지정하고, 모델이 학습한 초기 자세에서 홈을 등록하세요.']
    stack=[]
    for line in body.splitlines():
        match=re.match(r'^( *)([A-Za-z_][A-Za-z0-9_]*):',line)
        if match:
            indent=len(match[1]);key=match[2]
            while stack and stack[-1][0]>=indent:stack.pop()
            path=[entry[1] for entry in stack]+[key]
            result.append(' '*indent+'# '+comment(path));stack.append((indent,key))
        result.append(line)
    return '\n'.join(result)+'\n'
