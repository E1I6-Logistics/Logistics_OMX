from pathlib import Path
import subprocess,tempfile,os,ast,sys,contextlib,io
script=Path(__file__).with_name('apply_mqtt_watchdog.py').resolve()
fixture='''import os, time, json\ncurrent_state = ("IDLE" if success and (robot_id not in WORKSTATION_IDS or workstation_ready()) else "ERROR")\nif current_state == "ERROR":\n    pass\ndef execute_orders(job_id, orders):\n    started = time.monotonic()\n    total_quantity = sum(qty for _, qty in orders)\n    completed_before = 0\n    for index, (item, qty) in enumerate(orders, 1):\n        local_item = {"C": "A", "D": "B"}.get(item, item)\n        result = subprocess.run(["bash", str(new_order_script), local_item, str(qty)])\n        if result.returncode != 0: raise RuntimeError()\n        if not state_path.exists(): raise RuntimeError()\n        accepted = json.loads(state_path.read_text())\n        token = accepted["order_started_at_epoch"]\n        last_progress = None\n        while True:\n\n            state = json.loads(state_path.read_text())\n            if state['remaining']==0: break\n            time.sleep(0.5)\n\ndef execute_unload(job_id, items):\n    pass\n'''
for i in range(1,6):
 with tempfile.TemporaryDirectory() as td:
  root=Path(td); f=root/'lerobot_unified/omx_mqtt_client.py'; f.parent.mkdir(); f.write_text(fixture,encoding='utf-8')
  prev=os.getcwd();oldargs=sys.argv[:]
  try:
   os.chdir(root)
   code=compile(script.read_text(),str(script),'exec')
   def go(flags):
    sys.argv=[str(script)]+flags
    ns={'__name__':'__main__'}
    with contextlib.redirect_stdout(io.StringIO()):
     exec(code,ns)
   go(['--check'])
   assert f.read_text(encoding='utf-8')==fixture
   go([])
   new=f.read_text(encoding='utf-8');ast.parse(new)
   assert 'raise TimeoutError' in new and 'OMX_ROLLOUT_PID' in new
   try:go(['--check'])
   except SystemExit:pass
   else:raise AssertionError('re-application was allowed')
  finally:
   os.chdir(prev);sys.argv=oldargs
  print(f'[PASS] preflight/apply/syntax/idempotence round {i}/5')
print('[PASS] 5 fixture-based rounds; NOT physical/MQTT runtime testing')
