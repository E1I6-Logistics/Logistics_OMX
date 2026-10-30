"""Publish one explicit manual CLI order; no SDK imports or automatic retries."""
import json
import shutil
import subprocess
import uuid


def send_order(cfg, item, quantity=1):
    robot = cfg['device_id']
    allowed = {'omx1': ('A', 'B'), 'omx2': ('C', 'D'), 'omx3': ('E',), 'omx4': ('F',)}
    if not isinstance(item, str): raise ValueError('품목을 지정하세요: --item A 등')
    item = item.upper()
    if item not in allowed[robot]: raise ValueError(f'{robot} 품목은 {allowed[robot]}입니다.')
    if type(quantity) is not int or not 1 <= quantity <= 8: raise ValueError('수량은 1~8 정수입니다.')
    if cfg['role'] == 'unload' and quantity != 1:
        raise ValueError('OMX3/4 수동 명령은 하차 작업 시작용이며 수량 1만 사용합니다. 물품 1개 하차 보장이 아닙니다.')
    executable = shutil.which('mosquitto_pub')
    if executable is None: raise ValueError('mosquitto_pub가 없습니다. sudo apt install mosquitto-clients 로 설치하세요.')
    payload = {'job_id': 'cli-' + uuid.uuid4().hex, 'items': {item: quantity}}
    command = [executable, '-h', cfg['runtime']['broker_host'], '-p', str(cfg['runtime']['broker_port']),
               '-t', robot + '/command', '-q', '1', '-m', json.dumps(payload)]
    print(f'[ORDER] 실제 작업 전송: {robot} {payload}', flush=True)
    subprocess.run(command, check=True)
    print('[ORDER] 전송 완료. OMX의 ack/progress/result로 접수와 완료를 확인하세요.')
    return payload
