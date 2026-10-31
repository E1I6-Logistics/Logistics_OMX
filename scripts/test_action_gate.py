"""Unit-exec a synthetic patched send_next_action, proving no mock motor send on abort."""
import contextlib
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from apply_omx_fail_closed import patch, FILES
from test_fail_closed import LAUNCH, MQTT, CORE, BASE

src = patch(dict(zip(FILES, (LAUNCH, MQTT, CORE, BASE))))[FILES[2]]
namespace = {'__name__':'mock_core'}
exec(compile(src, '<mock core>', 'exec'), namespace)
f = namespace['send_next_action']
class Interp:
    def __init__(self):self.resets=0
    def reset(self):self.resets+=1
    def needs_new_action(self):return False
class Engine:
    def __init__(self):self.resets=0
    def reset(self):self.resets+=1
class MockRobot:
    def __init__(self):self.sent=[]
    def send_action(self, act):self.sent.append(act)

with tempfile.TemporaryDirectory() as temp:
    flag = Path(temp)/'abort'
    prev = os.environ.get('OMX_ABORT_FILE')
    try:
        os.environ['OMX_ABORT_FILE']=str(flag)
        for i in range(1, 6):
            flag.touch()
            interp=Interp(); eng=Engine(); robot=MockRobot()
            ctx=SimpleNamespace(policy=SimpleNamespace(inference=eng), hardware=SimpleNamespace(robot_wrapper=robot),processors=SimpleNamespace(robot_action_processor=lambda x:x))
            try:
                f(ctx, interp)
                raise AssertionError('abort did not throw')
            except RuntimeError as exc:
                assert 'no more motor actions' in str(exc)
            assert interp.resets==1 and eng.resets==1 and robot.sent==[]
            flag.unlink()
            assert f(ctx, interp)=={}
            assert len(robot.sent)==1
            print(f'[PASS] mock abort blocks send_action, normal path preserved {i}/5')
    finally:
        if prev is None:os.environ.pop('OMX_ABORT_FILE',None)
        else:os.environ['OMX_ABORT_FILE']=prev
