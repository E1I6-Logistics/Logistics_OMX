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
    'inspection_freeze_autofocus':'검사 중 활성 UVC 자동초점을 잠그고 종료·오류 시 원래 자동초점 복원: 고정초점 카메라는 변경 없음',
    'registration_adaptive':'배경 합의로 조명·잡음 보정, 기존 테두리의 작은 위치 오차와 얇은 선 구분: 빈 기준 자동 변경 없음',
    'registration_spatial_tolerance':'기존 기준의 테두리에서만 허용할 픽셀 위치 오차(128 정렬 영상, 최대 5): 평평한 바닥의 약한 물체 차이는 유지',
    'registration_min_blob_width':'얇은 테두리 선을 제거할 물체 핵심 폭(128 영상의 홀수 3~7): 2cm 물체의 면적은 열기 연산으로 복원',
    'registration_background_max_shift':'여러 배경 영역이 동의할 때만 허용할 동적 RGB 보정 한도(초과 시 UNKNOWN): 합의가 없으면 기존 조명 한도 사용',
    'registration_background_tolerance':'16개 배경 타일이 같은 조명 변화로 동의할 RGB 오차 한도(0~255)',
    'registration_background_min_tiles':'조명 보정에 필요한 최소 평탄 배경 타일 수: 3개 사분면 이상으로 분산돼야 함',
    'registration_background_fraction':'사용 가능한 배경 타일 중 같은 RGB 변화에 동의해야 할 최소 비율',
    'registration_noise_limit':'배경 합의 영역의 잡음 추정 한도: 초과 시 임계값을 계속 높이지 않고 UNKNOWN',
    'registration_full_chroma_fallback':'전체 시야에서 조명 차이만으로 거절될 때 색상·국소 RGB 근거로 물건 있음만 추가 확인: 빈 바구니 판정과 조명 허용값은 완화하지 않음',
    'registration_partial_chroma_fallback':'부분 시야에서 조명 차이만으로 막힐 때 밝기를 정규화한 색상 변화로 물건 있음만 추가 확인: EMPTY로 우회하지 않음',
    'registration_chroma_threshold':'색상 보조 판정의 차이 문턱(정규화 RGB 0~255): 조명 밝기와 전역 색조 차이는 별도 보정',
    'registration_chroma_component_pixels':'색상으로 물건 있음을 확인할 최소 연결 면적(128 정렬 영상 픽셀)',
    'registration_wall_color_limit':'전체 시야에서 독립된 테두리 3면 이상의 RGB 변화가 일치할 때만 허용할 조명 보정 한도(기본 12, 최대 16): 합의 실패 시 기존 내부 기준 사용',
    'camera_auto_reconnect':'영상 읽기 실패 시 모델 명령을 멈추고 같은 USB 카메라 재연결 시도: 토크·모터 모드는 변경하지 않음',
    'camera_reconnect_attempts':'카메라 자동 재연결 최대 횟수(1~3): 실패 시 복구 대기, 미완료 주문 자동 재실행 없음',
    'camera_reconnect_interval_s':'카메라 재연결 시도 사이 대기 시간(초)',
    'interrupted_order_action':'집기 중단 주문 처리: fail_and_idle=기록·실패 보존 후 홈 점검 및 새 주문 대기, require_ack=기존 수동 확인',
    'inspection_median_frames':'신선하게 정렬한 최근 검사 영상의 중앙값 장수(1/3/5): 3 권장, 움직임·오래된 영상이면 초기화',
    'inspection_control_poll_s':'검사 중 노출·게인 확인 간격(초): 복원 전에는 별도로 모든 설정을 다시 확인',
    'registration_partial_component_pixels':'부분 시야에서 물건 확정에 필요한 연결 면적: 추정 모서리·초점 차이로 인한 작은 변화는 UNKNOWN',
    'registration_partial_color_limit':'부분 시야의 중앙 RGB 밝기 차이 보정 한도: 넘으면 UNKNOWN',

    'registration_allow_partial':'부분 시야 검사 허용: true이면 보이는 영역만 판단하며 전체 비움으로 보고하지 않음',
    'registration_visible_confirmed':'사용자가 보이는 영역만 검사하는 범위를 확인했는지 여부',
    'registration_min_visible_ratio':'128 정렬 영상에서 요구할 최소 관측 비율: 보이지 않는 부분은 비교에서 제외',
    'registration_max_extrapolation':'상단 잘림 모서리 위치 추정의 최대 거리: ROI 크기에 대한 비율, 추정 영역은 비움 근거로 사용하지 않음',

    'registration_still_px':'확정 대기 중 기준 모서리에서 허용할 원본 영상 움직임(픽셀): 정렬 영상만 보고 정지 판단하지 않음',
    'registration_enabled':'이동·회전하는 바구니 정렬 검사 사용 여부: 변경 시 빈 기준 재등록 필요',
    'registration_search_pad':'기존 사각 ROI 밖으로 바구니를 찾을 추가 여유 픽셀: 원본 영상 기준',
    'registration_threshold_factor':'흰 바구니 외곽 후보의 밝기 문턱 계수: Otsu 자동 문턱에 곱함',
    'registration_min_area_ratio':'바구니 후보의 최소 면적: 기존 사각 ROI 면적 대비 비율',
    'registration_max_shift_px':'기준 바구니 중심에서 허용할 최대 이동 거리: 원본 영상 픽셀',
    'registration_max_rotation_deg':'기준 바구니에서 허용할 최대 영상 내 회전 각도(도)',
    'registration_scale_tolerance':'기준 대비 허용할 바구니 영상 면적 변화 비율',
    'registration_inner_margin':'128 정렬 영상에서 테두리 혼합 픽셀을 제외할 폭: 과도한 확대 금지',
    'registration_pixel_threshold':'정렬 후 물건 후보로 사용할 RGB 차이 문턱',
    'registration_weak_threshold':'정렬 후 약한 변화 확인 문턱: 물건 확정이 어려우면 UNKNOWN',
    'registration_component_pixels':'가는 선을 제거한 뒤 물건 확정에 필요한 연결 면적(128 정렬 영상 픽셀)',
    'registration_weak_pixels':'약한 변화 때문에 EMPTY를 보류할 연결 면적(128 정렬 영상 픽셀)',
    'registration_color_limit':'보정할 수 있는 중앙 RGB 밝기 차이 한도: 넘으면 UNKNOWN',

    "view":"노트북 브라우저용 읽기 전용 뷰어: omx_view 실행 시에만 네트워크 서버 시작",
    "bind":"뷰어가 받을 Pi IPv4 주소: 0.0.0.0=Pi의 모든 네트워크 인터페이스",
    "port":"뷰어 웹 포트: 노트북에서 http://Pi주소:포트/ 접속",
    "allow_ip":"영상 열람을 허용할 노트북 IPv4: DHCP로 바뀌면 수정",
    "max_width":"보기용 JPEG 최대 가로 픽셀: 모델 입력 영상 크기는 변경하지 않음",
    "jpeg_quality":"보기용 JPEG 품질(30~90): 낮추면 전송량 감소",
    "max_age_s":"이 시간보다 오래된 영상은 실시간으로 표시하지 않음(초)",
    "mode":"카운터 방식: cycle=모델 복귀 영역 기반(권장), ordered_stages=이전 수동 단계 방식",
    "departure_hold_s":"주요 관절이 복귀 영역 밖에 연속으로 머물러야 할 시간(초): 순간 흔들림 제외",
    "min_actions":"한 사이클에서 필요한 최소 모델 명령 전송 횟수: 정지/홈 보정 명령은 제외",
    "return_hold_s":"학습한 모델 복귀 영역에서 연속 정지를 확인할 시간(초)",
    "min_training_cycles":"모델 보정에서 사람이 정상 완료를 확인할 최소 사이클 수: 기본 3",
    "tolerance_padding":"확인한 복귀 흔들림 범위에 추가할 여유(정규화 단위)",
    "min_return_tolerance":"복귀 영역 관절별 최소 허용 폭(정규화 단위)",
    "max_return_tolerance":"자동 계산 가능한 복귀 허용 폭의 최대값: 초과하면 저장 거절",
    "calibration_hold_s":"정상 완료 확인 후 복귀 자세를 안정적으로 관측할 시간(초)",
    "calibration_settle_timeout_s":"Enter 후 모델 명령을 멈추고 연속 안정 구간을 기다릴 최대 시간(초): 초과하면 정지 상태로 재확인 대기",
    "center":"모델 정상 복귀의 중앙 자세: 고정 시작 홈과 별개, 자동 계산",
    "confirmed_cycles":"사람이 실제 정상 완료를 확인한 학습 사이클 수",
    "min_excursion":"확인한 모델 사이클 중 최소 주요 관절 이동 폭(정규화 단위)",
    "source":"이 복귀 프로필을 만든 자료의 종류: 정상 확인 모델 실행",
    "confirmation_s":"빈 상태/물건 있음을 확정하기 위한 최소 실제 관측 시간(초)",
    "max_frame_age_s":"바구니 판정에 허용할 프레임 최대 나이(초): 오래된 영상은 UNKNOWN",
    "workflow":"품목별 실제 작업 단계: 홈 이탈만으로 카운트하지 않고 등록 자세를 순서대로 관측",
    "stages":"실제 집기·놓기 등 작업 순서의 관절 자세 목록: 최소 두 개, 직접 관측하여 등록",
    "name":"작업 단계 또는 복귀 경로 이름",
    "samples":"해당 작업 자세를 연속으로 확인할 관측 수: 기본 2, 긴 관측 공백은 초기화",
    "unload":"OMX3/4 반복 하차·빈 바구니 영상 판정: 물품 개수는 영상 변화로 추정하며 확정값이 아님",
    "camera":"빈 바구니를 관측할 고정 상단 카메라 입력 이름",
    "roi":"바구니 내부 영상 영역 [왼쪽 x, 위쪽 y, 가로, 세로]: 팔·컨베이어·외부 배경 제외",
    "vision_method":"검사 방식: low_template=검사 전용 저노출·빈 기준 픽셀 비교, rgb_roi=대비·경계, legacy=이전 방식. 모델 구동에는 원래 카메라 설정 복원",
    "roi_polygon":"바구니 내부 다각형: 원본 영상의 [[x,y],...] 절대 좌표(볼록, 3~12점). null이면 사각형 ROI 전체 사용",
    "full_view_confirmed":"바구니 내부 전체와 정차 오차까지 검사 영역에 들어오는지 현장 확인 여부: false이면 새 검사 방식은 EMPTY 확정/자동 하차 불가",
    "local_pixel_threshold":"작은 영역의 명암 차이를 변화로 볼 최소 값(0~255): 낮을수록 민감하며 노이즈도 증가",
    "local_occupied_ratio":"20×20 검사 타일 내부 변화 픽셀 비율: 연결된 변화 덩어리 조건과 함께 사용",
    "min_component_pixels":"물건 후보로 인정할 연결된 변화의 최소 픽셀 수: 320×240 검사 영상 기준, 작은 변화도 빈 판정은 보류",
    "edge_threshold":"검사용 경계 검출의 낮은 임계값: 카메라/모델 영상은 바꾸지 않음",
    "contrast_clip_limit":"검사용 ROI의 국소 대비 보정 강도: 모델 원본 영상에는 적용하지 않음",
    "max_clipped_ratio":"검사 영역에서 RGB 모두 250 이상인 과노출 픽셀 비율 한도: 초과 시 EMPTY 확정 금지",
    "low_exposure_enabled":"홈에서 검사할 때만 센서를 저노출로 전환: 검사 후 원래 설정·새 프레임 확인 전 모델 동작 금지",
    "inspection_exposure":"검사 전용 고정 노출값: 카메라별 단위가 다르므로 exposure 조회·low-snapshot 시험 후 지정",
    "exposure_discard_frames":"노출 전환/복원 뒤 버릴 서로 다른 새 프레임 수: 이전 영상 재사용 방지",
    "exposure_settle_s":"전환/복원 뒤 신선한 영상의 평균 밝기가 안정돼야 할 연속 시간(초)",
    "exposure_switch_timeout_s":"원래/검사 영상 안정 확인의 단계별 제한 시간(초): 실패 시 모델 동작 차단",
    "exposure_restore_luma_tolerance":"자동 검사 뒤 원래 영상과 비교할 평균 밝기 허용 차이(0~255): 자동노출 복원 확인",
    "inspection_min_luma":"저노출 기준/현재 ROI의 최소 평균 밝기: 지나치게 어두우면 UNKNOWN",
    "inspection_max_dark_ratio":"저노출 ROI의 RGB 모두 5 이하인 픽셀 비율 한도: 초과 시 UNKNOWN",
    "reference":"현장에서 빈 바구니 확인 후 저장한 기준 NPZ 영상: 자동 생성",
    "reference_binding":"기준 영상의 홈·장치·카메라 연결 해시: 자동 생성",
    "pixel_threshold":"기준 영상과 픽셀 색상 차이를 변화로 볼 최소 값(0~255)",
    "empty_ratio":"변화 픽셀 비율이 이 값 이하면 빈 바구니 후보",
    "occupied_ratio":"변화 픽셀 비율이 이 값 이상이면 물건 있음 후보: 두 기준 사이는 판정 보류",
    "illumination_limit":"기준 대비 전체 평균 색상 변화 허용값: 조명·노출이 크게 바뀌면 판정 보류",
    "stability_delta":"연속 검사 영상의 평균 픽셀 변화 허용값: 움직임·흔들림 중 판정 보류",
    "sample_interval_s":"홈에서 바구니를 검사할 간격(초): 모델 동작 중 영상 추가 처리 없음",
    "stable_samples":"빈 상태 또는 물건 있음을 연속으로 확인할 프레임 수",
    "inspect_timeout_s":"홈에서 영상 판정을 기다리는 최대 시간(초): 불명확하면 성공 처리 안 함",
    "max_attempts":"하차 주문 한 건에서 집기를 시도할 최대 횟수",
    "max_no_change":"집기 전후 바구니 변화가 없는 연속 실패 허용 횟수",
    "job_timeout_s":"여러 번 집기를 포함한 하차 주문 전체 제한 시간(초)",
    "require_quantity_match":"빈 바구니라도 추정 이동 횟수가 주문 수량과 다르면 불일치로 중단",
    "auto_home":"오류 후 건강한 모터·허용 홈 접근 영역 또는 시험된 경로에서만 자동 복귀 시도",
    "poll_s":"복구 대기 중 상태 점검 간격(초): 모델을 다시 로딩하지 않음",
    "routes":"현장에서 직접 등록하고 전체 이동 시험을 통과한 복귀 경로 목록",
    "points":"현재 지점에서 홈 방향 순서의 여섯 관절 경유 자세: 마지막은 등록 홈",
    "radius":"등록 선분 주변 복귀 허용 폭(정규화 단위): 모르는 자세에서는 복귀 거절",
    "approved":"전체 경로 실물 시험 성공 후 저장되는 연결 해시: 수동 true 설정 금지",
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
    "hold_s":"등록 작업 단계 통과 후 홈 복귀 연속 정지 확인 시간(초): 피킹·반복 하차 공통",
    "unload_hold_s":"이전 단일 하차 경로의 정지 시간(초): 새 반복 하차는 hold_s와 영상 검사 사용",
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
    "counting":"주요 관절 이동·전송 횟수·모델 복귀 영역으로 동작 사이클 카운트: 실제 물품 성공 수와 별개",
    "leave_tolerance":"사이클 출발로 인정할 주요 관절 이동 폭: 손목·그리퍼만 움직이는 것은 제외",
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
    "cycle_timeout_s":"피킹 또는 하차 한 동작 사이클 제한 시간(초): 여러 번 하차 전체는 unload.job_timeout_s",
    "order_per_unit_s":"피킹 주문 감시에서 물품 1개당 허용 시간(초)",
    "order_load_grace_s":"품목 주문 시작·모델 로딩을 위한 추가 여유 시간(초)",
    "startup_timeout_s":"모델·장치 준비 완료를 기다리는 최대 시간(초)",
    "heartbeat_timeout_s":"제어 프로세스 생존 신호가 없어도 기다리는 시간(초)",
    "shutdown_timeout_s":"자식 프로세스 정상 종료 후 강제 정리까지 기다리는 시간(초)",
    "recovery":"오류 후 감독 프로그램의 재시도 간격: 미완료 물리 작업은 자동 재집기 안 함",
    "retry_initial_s":"오류 후 첫 재시도 대기 시간(초)",
    "release_delay_s":"수동 복귀용 전체 토크 OFF 전 대기 시간(초): 팔 지지 후 실행, 기본 10초",
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
    if key=='profiles' and 'counting' in path:return '품목별 모델 복귀 프로필: omx_cycle_calibrate로 자동 계산·저장'
    if len(path)>=3 and path[-2]=='profiles' and 'counting' in path:return '품목 '+key+'의 모델 복귀 영역: 자동 저장, 고정 초기 자세와 별개'
    if key=='binding' and 'counting' in path:return '모델 복귀 프로필의 홈·모델·보정 연결 해시: 자동 생성'
    if key in JOINT_NAMES and 'center' in path:return JOINT_NAMES[key]+' 모델 복귀 중앙값: 그리퍼는 카운트에서 제외'
    if key in JOINT_NAMES and 'counting' in path:return JOINT_NAMES[key]+' 모델 복귀 허용 폭(정규화 단위)'
    if key=='items' and 'workflow' in path:return '외부 품목 이름별 작업 단계: OMX1 A/B 반대 모델 매핑은 그대로 유지'
    if key=='binding' and 'workflow' in path:return '작업 단계 등록의 홈·모델·보정 연결 해시: 자동 생성'
    if key=='target' and 'workflow' in path:return '이 작업 단계에서 관측해야 할 여섯 관절 자세: 모델 재학습과 별개'
    if key=='tolerance' and 'workflow' in path:return '이 작업 단계의 자세 허용 오차(정규화 단위): 이웃 단계·홈 영역과 겹치지 않게 설정'
    if key in JOINT_NAMES:
        meaning="작업 단계 목표" if 'workflow' in path else "복귀 경유점" if 'points' in path else "홈 목표" if 'target' in path else "홈 복귀 허용 오차"
        return JOINT_NAMES[key]+" 관절의 "+meaning+"(정규화 단위, 각도 아님)"
    if path[0]=='view' and key=='fps':return '보기 전용 초당 전송 상한(0.2~2): 모델 제어 주기와 별개'
    if path[0]=='view' and key=='camera':return '보기 전용 입력 이름: auto=top 우선, 없으면 front; 모델 입력 이름은 변경하지 않음'
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
        match=re.match(r'^( *)(?:- )?([A-Za-z_][A-Za-z0-9_]*):',line)
        if match:
            indent=len(match[1]);key=match[2]
            while stack and stack[-1][0]>=indent:stack.pop()
            path=[entry[1] for entry in stack]+[key]
            result.append(' '*indent+'# '+comment(path));stack.append((indent,key))
        result.append(line)
    return '\n'.join(result)+'\n'
