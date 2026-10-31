#!/usr/bin/env python3
"""Read-only static + isolated control-logic tests. Never talks to motors/MQTT."""
import ast, argparse, importlib.util, os, pathlib, tempfile, unittest, sys, math
R=pathlib.Path.cwd(); B=R/'lerobot_unified/src/lerobot/rollout/strategies/base.py'; C=R/'lerobot_unified/src/lerobot/rollout/configs.py'; IPC=R/'lerobot_unified/src/lerobot/rollout/workstation_ipc.py'
class Suite(unittest.TestCase):
 def test_files_parse(self):
  for p in [B,C,IPC,R/'lerobot_unified/omx_mqtt_client.py']:
   with self.subTest(path=p): ast.parse(p.read_text(encoding='utf-8'))
 def test_timeout_properties_resolve(self):
  config=ast.parse(C.read_text(encoding='utf-8'))
  cls=next(x for x in config.body if isinstance(x,ast.ClassDef) and x.name=='BaseStrategyConfig')
  attrs={t.id for n in cls.body if isinstance(n,ast.AnnAssign) for t in [n.target] if isinstance(t,ast.Name)}
  control=ast.parse(B.read_text(encoding='utf-8'))
  for n in ast.walk(control):
   if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Attribute) and isinstance(n.value.value,ast.Name) and n.value.value.id=='self' and n.value.attr=='config' and 'timeout' in n.attr:
    self.assertIn(n.attr,attrs, f'undefined config attribute: {n.attr}')
 def test_fail_closed_home(self):
  tree=ast.parse(B.read_text(encoding='utf-8')); cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='BaseStrategy')
  f=next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='_max_home_error')
  import logging
  ns={'Mapping':dict,'logger':logging.getLogger('test'), 'math':math}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[f],type_ignores=[])),'<isolated home>','exec'),ns)
  fun=ns['_max_home_error']
  self.assertEqual(fun({'j1':0.,'j2':2.},{'j1':0.,'j2':0.}),2.)
  self.assertIsNone(fun({'j1':0.},{'j1':0.,'j2':0.}))
  self.assertIsNone(fun({'j1':float('nan')},{'j1':0.}))
  self.assertIsNone(fun({'j1':float('inf')},{'j1':0.}))
 def test_timeouts_and_stalls(self):
  t=B.read_text(encoding='utf-8')
  for token in ['pickup_cycle_timeout_s','workstation_job_timeout_s','engine.pause()','return_detector_armed']:
   self.assertIn(token,t)
  self.assertIn('write_status("ERROR"',t)
 def test_ipc_atomic_and_session(self):
  spec=importlib.util.spec_from_file_location('ipc_test',IPC); mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  with tempfile.TemporaryDirectory() as tmp:
   p=pathlib.Path(tmp)/'nested'/'status.json'
   mod.atomic_json(p, mod.new_status('run-new','READY'))
   self.assertTrue(mod.valid_ready(mod.read_json(p),'run-new'))
   self.assertFalse(mod.valid_ready(mod.read_json(p),'run-old'))
 def test_launch_config(self):
  t=(R/'lerobot_unified/run_omx_backend.sh').read_text(encoding='utf-8')
  self.assertIn('--strategy.pickup_cycle_timeout_s=',t)
  for name in ['run_e_rollout.sh','run_f_rollout.sh']:
   self.assertIn('--strategy.workstation_job_timeout_s=',(R/'lerobot_unified'/name).read_text(encoding='utf-8'))
parser=argparse.ArgumentParser();parser.add_argument('--runs',type=int,default=5);a=parser.parse_args()
if not 1 <= a.runs <= 100: parser.error('--runs must be 1..100')
fail=0
for i in range(a.runs):
 print(f'\n=== 반복 {i+1}/{a.runs} ===',flush=True)
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(Suite)
 result=unittest.TextTestRunner(verbosity=1).run(suite)
 fail+=len(result.failures)+len(result.errors)
 if not result.wasSuccessful(): break
print(f'\nRESULT: {"PASS" if fail==0 else "FAIL"} / attempted_runs={i+1} / checks_per_run=6')
sys.exit(bool(fail))
