"""Korean operator display; protocol codes and original full logs stay intact."""
PHASES={'CALIBRATION_CHECK':'시작 준비·사이클 보정 확인','RECONCILING':'이전 작업 정리','MOTOR_CHECK':'모터 점검','HOME_RETURN':'홈 복귀 중',
        'HOME_VERIFIED':'홈 확인 완료','MODEL_LOADING':'모델·카메라 준비 중','MQTT_CONNECTING':'관제 통신 연결 중',
        'READY':'주문 대기 준비 완료','RECOVERY_WAIT':'복구 대기','VERIFYING_RECOVERY':'복구 상태 재확인',
        'CHILDREN_STOPPED':'하위 프로그램 정리 완료','STOPPED':'사용자 정지','CAMERA_RESTORE':'카메라 설정 복원 중'}
TEXT={
 'model return region missing/changed; run omx_cycle_calibrate':'유효한 사이클 보정을 자동 복원하지 못했습니다. 보정 명령: omx_cycle_calibrate',
 'Outside startup Home area; local positioning required':'시작 홈 허용 범위 밖입니다. 현재 자세 확인 필요',
 'Outside approved Home approach area; position arm locally first':'홈 접근 범위 밖입니다. 자세를 확인하세요',
 'Home movement timed out':'홈 복귀 제한 시간 초과',
 'Home feedback stalled; no next action':'관절 피드백이 지연되어 추가 이동을 중지합니다',
 'Home following error exceeded limit':'관절이 목표를 따라가지 못해 이동을 중지합니다',
 'Motor hardware error':'모터 자체 오류',
 'Motor mode mismatch; no automatic rewrite':'모터 운전 모드 불일치. 자동 변경하지 않습니다',
 'Torque OFF: support arm and use local arm command; automatic startup enable is disabled':'토크 OFF 상태입니다. 시작 토크 자동 활성화 설정이 꺼져 있습니다',
 'Torque OFF after startup or during passive recheck; local arm command required':'운영 중 토크 OFF 또는 관찰 점검 상태입니다. 현장 토크 활성화가 필요합니다',
 'Mixed/changed torque state; inspect locally before enabling':'관절별 토크 상태가 서로 다릅니다. 현장 확인 필요',
 'Startup pose is moving; no torque enable':'팔이 움직이고 있어 토크를 켜지 않습니다',
 'Startup pose feedback gap; no torque enable':'시작 위치 피드백 지연으로 토크를 켜지 않습니다',
 'Unverified startup torque/order state; inspect locally and request resume':'이전 토크 활성화·주문 상태가 미확인입니다. 현장 확인 후 omx_resume 필요',
 'Automatic restart limit reached; inspect and request resume':'자동 재시도 상한 도달. 상태 확인 후 omx_resume 필요',
 'Arm Home recovered with gripper held; inspect cargo then request resume':'팔 홈 복귀 완료, 그리퍼는 유지했습니다. 물품 확인 후 omx_resume 필요',
 'model not loaded; MQTT rejects new jobs':'모델 미로딩 상태이며 새 주문은 거절합니다',
 'Home and registration rechecked; starting a fresh idle session, no job replay':'홈·등록 상태 재확인 완료. 이전 주문 재실행 없이 새 대기 세션을 시작합니다',
 'Previous jobs failed and active IPC cleared; identities archived/preserved; no replay':'이전 작업 실패 처리·활성 명령 정리 완료. 주문 ID·증거 보관, 재실행 없음',
 'Child process cleanup pending; new hardware session blocked':'하위 프로그램 정리가 끝나지 않아 새 장치 연결을 차단합니다',
 'Another runtime/calibration session owns':'다른 실행기·보정 프로그램이 장치를 사용 중입니다',
 'tool will not enable it automatically':'이 명령은 토크를 자동으로 켜지 않습니다. omx_arm을 먼저 실행하세요',
 'Torque OFF':'토크 OFF',
 'Rollout startup exit=':'모델 실행기 시작 실패, 종료 코드=',
 'Rollout exit=':'모델 실행기 종료 코드=',
 'Rollout heartbeat stalled':'모델 실행기 응답 지연',
 'Rollout not READY in time':'모델 실행기 준비 제한 시간 초과',
 'retry in ':'다음 점검까지 ',
 'Control loop is running slower':'제어 반복 작업이 목표보다 느립니다',
 'than the target FPS':'목표 주파수',
 'Robot control might be unstable. Common causes are: 1) Camera FPS not keeping up 2) Policy inference (action or text) taking too long 3) CPU starvation':'일부 반복 작업이 시간 예산을 초과했습니다. 카메라·통신 대기, 추론 시간, CPU 상태를 확인하세요',
 'Camera exposure restoration pending; stop manager, use omx_basket restore, inspect cargo, then restart':'카메라 설정 복원 대기입니다. 자동 복원 결과를 확인하거나 실행기를 멈추고 omx_basket restore를 실행하세요',
}


def korean(line):
    for source,target in TEXT.items():line=line.replace(source,target)
    for phase,label in PHASES.items():line=line.replace('[STATE] '+phase,'[STATE] '+phase+' · '+label)
    return line
