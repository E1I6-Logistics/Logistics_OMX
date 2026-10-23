"""Offline test of MQTT worker-to-controller command handshake."""
import ast
import importlib.util
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ipc',ROOT/'lerobot_unified/src/lerobot/rollout/workstation_ipc.py')
ipc = importlib.util.module_from_spec(spec);spec.loader.exec_module(ipc)


class TestWorker(unittest.TestCase):
    def test_start_done_clear_returns_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd=Path(tmp)/'cmd.json';status=Path(tmp)/'status.json';sess='s123';job='job-uuid'
            ipc.atomic_json(status,ipc.new_status(sess,'READY'))
            source=(ROOT/'lerobot_unified/omx_mqtt_client.py').read_text()
            tree=ast.parse(source)
            fun=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='execute_unload')
            env={'robot_id':'omx3','WORKSTATION_IDS':('omx3','omx4'),
                 'workstation_session':sess,'workstation_command':cmd,
                 'workstation_state':lambda:ipc.read_json(status),
                 'workstation_ready':lambda:ipc.valid_ready(ipc.read_json(status),sess),
                 '_workstation_process_alive':lambda *_:True,
                 'atomic_json':ipc.atomic_json,'time':time,
                 'os':__import__('os')}
            code=compile(ast.fix_missing_locations(ast.Module(body=[fun],type_ignores=[])),'<client worker>','exec')
            exec(code,env)
            transitions=[]
            def controller():
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    data=ipc.read_json(cmd)
                    if data.get('command')=='START':
                        transitions.append('START')
                        ipc.atomic_json(status,ipc.new_status(sess,'RUNNING',job))
                        time.sleep(.06)
                        ipc.atomic_json(status,ipc.new_status(sess,'DONE',job,reason='home_stillness_after_departure'))
                        break
                    time.sleep(.005)
                while time.monotonic()<deadline:
                    data=ipc.read_json(cmd)
                    if data.get('command')=='CLEAR':
                        transitions.append('CLEAR')
                        ipc.atomic_json(status,ipc.new_status(sess,'READY'))
                        return
                    time.sleep(.005)
            t=threading.Thread(target=controller,daemon=True);t.start()
            result=env['execute_unload'](job,{'A':1})
            t.join(timeout=1)
            self.assertEqual(transitions,['START','CLEAR'])
            self.assertEqual(result['phase'],'DONE')
            self.assertEqual(ipc.read_json(status)['phase'],'READY')

if __name__=='__main__':unittest.main()
