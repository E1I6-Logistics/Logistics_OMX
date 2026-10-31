"""Publish one explicit manual CLI order; no SDK imports or automatic retries."""
import json
import shutil
import subprocess
import uuid


def send_order(cfg, item=None, quantity=1, until_empty=False):
    robot = cfg['device_id']
    allowed = {'omx1': ('A', 'B'), 'omx2': ('C', 'D'), 'omx3': ('E',), 'omx4': ('F',)}
    if until_empty:
        from .basket import options
        if not options(cfg)['inspection_enabled']:raise ValueError('바구니 검사 OFF: omx_unload 1 처럼 동작 횟수 1~8을 지정하세요.')
        if cfg['role']!='unload': raise ValueError('빈 바구니까지 반복은 OMX3/4 하차만 가능합니다.')
        if quantity!=1: raise ValueError('--until-empty와 --quantity는 함께 지정하지 마세요.')
        items={}
    else:
        if not isinstance(item, str): raise ValueError('품목을 지정하세요: --item A 등')
        item = item.upper()
        if item not in allowed[robot]: raise ValueError(f'{robot} 품목은 {allowed[robot]}입니다.')
        items={item:quantity}
    if type(quantity) is not int or not 1 <= quantity <= 8: raise ValueError('수량은 1~8 정수입니다.')
    executable = shutil.which('mosquitto_pub')
    if executable is None: raise ValueError('mosquitto_pub가 없습니다. sudo apt install mosquitto-clients 로 설치하세요.')
    payload = {'job_id': 'cli-' + uuid.uuid4().hex, 'items': items}
    command = [executable, '-h', cfg['runtime']['broker_host'], '-p', str(cfg['runtime']['broker_port']),
               '-t', robot + '/command', '-q', '1', '-m', json.dumps(payload)]
    print(f'[ORDER] 실제 작업 전송: {robot} {payload}', flush=True)
    subprocess.run(command, check=True)
    print('[ORDER] 전송 완료. OMX의 ack/progress/result로 접수와 완료를 확인하세요.')
    return payload
