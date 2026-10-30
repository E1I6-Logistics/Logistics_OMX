"""Hardware-free diagnostics tests: rates, source hooks, bounded logs and opt-out."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from omx_manager import config as C, diagnostics as D, logs as L
from omx_manager.cli import invoke

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('tested_metrics',ROOT/'lerobot_unified/src/lerobot/utils/runtime_metrics.py')
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

@pytest.fixture
def metrics(monkeypatch):
    counters=M.Counters();monkeypatch.setattr(M,'COUNTERS',counters);monkeypatch.setattr(M,'ENABLED',True)
    return counters

def test_counters_are_thread_safe_and_track_durations():
    c=M.Counters()
    workers=[threading.Thread(target=lambda:[c.event('frame',1.) for _ in range(1000)]) for _ in range(4)]
    for w in workers:w.start()
    for w in workers:w.join()
    c.duration('chunk',.1);c.duration('chunk',.3)
    assert c.snapshot()['events']['frame']==(4000,1.)
    assert c.snapshot()['timings']['chunk']==(2,.4,.3)

def test_disabled_metrics_do_not_collect_or_start_thread(monkeypatch,tmp_path):
    monkeypatch.setattr(M,'ENABLED',False);c=M.Counters();monkeypatch.setattr(M,'COUNTERS',c)
    camera=NS(_omx_metric_name='top')
    M.event('loop');M.camera_event(camera,'captured')
    robot=NS(cameras={'top':camera});M.start_metrics(robot,threading.Event(),30)
    assert not c.snapshot()['events'] and not hasattr(robot,'_omx_metrics_close')

def test_chunk_wrapper_preserves_result_arguments_and_exception(metrics):
    value=object()
    @M.measure_chunk
    def predict(batch,*,flag):assert batch is value and flag;return value
    assert predict(value,flag=True) is value
    @M.measure_chunk
    def fail():raise ValueError('original failure')
    with pytest.raises(ValueError,match='original failure'):fail()
    assert metrics.snapshot()['timings']['act_chunk'][0]==2

def test_camera_capture_and_publish_are_distinct(metrics):
    camera=NS(_omx_metric_name='wrist')
    for i in range(30):
        M.camera_event(camera,'captured')
        if i%2==0:M.camera_event(camera,'published')
    events=metrics.snapshot()['events']
    assert events['camera/wrist/captured'][0]==30
    assert events['camera/wrist/published'][0]==15

@pytest.mark.parametrize('kind',['opencv','realsense'])
def test_actual_camera_hooks_are_in_background_capture_loop(kind):
    tree=ast.parse((ROOT/f'lerobot_unified/src/lerobot/cameras/{kind}/camera_{kind}.py').read_text())
    method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_read_loop')
    calls=[n for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='camera_event']
    assert sorted(n.args[1].value for n in calls)==['captured','published']
    read=next(n for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='_read_from_hardware')
    assert read.lineno<min(n.lineno for n in calls)

def test_actual_act_chunk_is_measured_but_cached_action_is_not():
    tree=ast.parse((ROOT/'lerobot_unified/src/lerobot/policies/act/modeling_act.py').read_text())
    methods={n.name:n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in ('select_action','predict_action_chunk')}
    assert any(isinstance(n,ast.Name) and n.id=='measure_chunk' for n in methods['predict_action_chunk'].decorator_list)
    assert not any(isinstance(n,ast.Name) and n.id=='measure_chunk' for n in methods['select_action'].decorator_list)

def test_snapshot_thread_never_reads_camera_or_motor(metrics,monkeypatch,tmp_path):
    path=tmp_path/'metrics.json';monkeypatch.setenv('OMX_METRICS_PATH',str(path))
    camera=NS(fps=30,processing_fps=15,read=Mock(side_effect=AssertionError('hardware forbidden')))
    robot=NS(cameras={'top':camera},get_observation=Mock(side_effect=AssertionError('hardware forbidden')))
    M.start_metrics(robot,threading.Event(),30,interval=.02)
    try:
        deadline=time.monotonic()+2
        while not path.exists() and time.monotonic()<deadline:time.sleep(.005)
        data=json.loads(path.read_text());assert data['pid']==os.getpid()
        assert data['cameras']['top']['processing_fps']==15
    finally:robot._omx_metrics_close()
    camera.read.assert_not_called();robot.get_observation.assert_not_called()
    assert not list(tmp_path.glob('*.tmp'))

def test_failed_snapshot_write_does_not_propagate(metrics,monkeypatch,tmp_path,capsys):
    monkeypatch.setenv('OMX_METRICS_PATH',str(tmp_path/'blocked'/'metrics.json'))
    monkeypatch.setattr(Path,'write_text',Mock(side_effect=OSError('disk unavailable')))
    robot=NS(cameras={});M.start_metrics(robot,threading.Event(),30,interval=.01)
    time.sleep(.04);robot._omx_metrics_close()
    assert capsys.readouterr().out.count('Snapshot unavailable')==1

def test_proc_parser_handles_spaces_in_process_name(tmp_path):
    folder=tmp_path/'42';folder.mkdir();fields=['0']*24
    fields[0]='S';fields[11]='100';fields[12]='20';fields[19]='500'
    (folder/'stat').write_text('42 (python worker) '+' '.join(fields))
    (folder/'status').write_text('VmRSS: 2048 kB\nThreads: 3\n')
    (folder/'cmdline').write_bytes(b'python\0-m\0lerobot.scripts.lerobot_rollout\0')
    assert D.read_process(42,tmp_path)==dict(pid=42,start_ticks=500,cpu_ticks=120,rss_mb=2.,threads=3)
    (folder/'cmdline').write_bytes(b'unrelated-command')
    assert D.read_process(42,tmp_path) is None

@pytest.fixture
def sampler(monkeypatch,tmp_path):
    cfg=C.load(ROOT/'params/omx4.yaml');cfg['diagnostics'].update(interval_s=2.,stale_s=6.);monkeypatch.setattr(D,'state_dir',lambda _:tmp_path)
    monkeypatch.setattr(D,'read_system',lambda:{})
    monkeypatch.setattr(D.shutil,'which',lambda _:None)
    process=dict(pid=42,start_ticks=500,cpu_ticks=100,rss_mb=2.,threads=3)
    monkeypatch.setattr(D,'read_process',lambda _:dict(process))
    (tmp_path/'heartbeat.json').write_text(json.dumps({'pid':42}))
    def put(stamp,frames,loops,sends,chunks=0,total=0.,pid=42,start=500):
        data={'pid':pid,'process_start_ticks':start,'monotonic':stamp,'target_hz':30,
              'cameras':{'top':{'configured_fps':30,'processing_fps':15}},
              'events':{'loop':[loops,stamp],'action_sent':[sends,stamp],
                        'camera/top/captured':[frames,stamp],'camera/top/published':[frames//2,stamp]},
              'timings':{'act_chunk':[chunks,total,.2]}}
        (tmp_path/'metrics.json').write_text(json.dumps(data))
    return D.Sampler(cfg),put,process

def test_rates_are_based_on_new_snapshots_and_chunk_calls(sampler):
    s,put,p=sampler;put(10,0,0,0)
    assert s.sample(10)['metrics']['loop_hz'] is None
    put(12,60,40,20,1,.2)
    p['cpu_ticks']+=os.sysconf('SC_CLK_TCK')*4
    data=s.sample(12);m=data['metrics']
    assert m['cameras']['top']['capture_fps']==30
    assert m['cameras']['top']['published_fps']==15
    assert m['loop_hz']==20 and m['action_send_hz']==10
    assert m['act_chunk_ms']==200 and data['control_cpu_pct']==200
    assert s.sample(13)['metrics']['loop_hz'] is None
    assert s.sample(20)['metrics'] is None

@pytest.mark.parametrize('change',['pid','start','stale'])
def test_stale_or_other_process_metrics_are_not_displayed(sampler,change):
    s,put,p=sampler;put(10,0,0,0,pid=43 if change=='pid' else 42,start=501 if change=='start' else 500)
    assert s.sample(20 if change=='stale' else 10)['metrics'] is None

def test_disabled_missing_metrics_and_render_are_honest(sampler):
    s,_,p=sampler;data=s.sample(10)
    assert data['metrics'] is None
    assert 'unavailable' in D.render(data)
    assert 'CPU=--' in D.render(data)

def test_filter_hides_heartbeat_and_keeps_traceback_and_jobs():
    f=L.LogFilter()
    assert not f.accepts("[SEND] omx1/status: {'state': 'IDLE'}")
    assert f.accepts("[SEND] omx1/status: {'state': 'ERROR'}")
    for line in ['Traceback (most recent call last):','  File "x.py", line 1','    fail()','ValueError: motor fault']:
        assert f.accepts(line)
    assert f.accepts("[SEND] omx1/result: {'job_id':'1','success':True}")

def test_cadence_warning_is_throttled_only_in_important_view(monkeypatch):
    f=L.LogFilter();now=[0.];monkeypatch.setattr(L.time,'monotonic',lambda:now[0])
    warning='Control loop is running slower (12 Hz) than the target FPS (30 Hz)'
    assert f.accepts(warning)
    assert not f.accepts(warning)
    now[0]=11;assert f.accepts(warning)

def test_capture_preserves_full_log_but_filters_important(tmp_path):
    capture=L.Capture(tmp_path,True)
    child=subprocess.Popen([sys.executable,'-c',"print(\"[SEND] omx1/status: {'state': 'IDLE'}\");print('[ERROR] camera failed');print('Traceback (most recent call last):');print('  File x.py');print('ValueError: failed')"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    capture.attach(child,'rollout');child.wait(timeout=3);capture.close()
    full=(tmp_path/'full.log').read_text();important=(tmp_path/'important.log').read_text()
    assert "'IDLE'" in full and "'IDLE'" not in important
    assert 'camera failed' in important and 'File x.py' in important and 'ValueError' in important

def test_bounded_log_queue_drops_instead_of_waiting(monkeypatch,tmp_path):
    monkeypatch.setattr(L.Capture,'_write',lambda self:self.stop.wait())
    c=L.Capture(tmp_path)
    try:
        for _ in range(600):c.emit('rollout','line')
        assert c.queue.qsize()==128 and c.dropped==472
    finally:c.close()

def test_read_log_initial_tail(tmp_path,capsys):
    p=tmp_path/'important.log';p.write_text('one\ntwo\nthree\n')
    L.view(p,lines=2);assert capsys.readouterr().out=='two\nthree\n'

def test_monitor_and_logs_need_no_registered_home_or_device(monkeypatch,capsys):
    monkeypatch.setattr('omx_manager.cli.FollowerBus',Mock(side_effect=AssertionError('hardware forbidden')))
    cfg=ROOT/'params/omx4.yaml';invoke(cfg,'monitor',samples=1)
    assert 'omx4' in capsys.readouterr().out

@pytest.mark.parametrize('interval',[0,.1,float('nan'),float('inf')])
def test_invalid_monitor_interval_rejected(interval):
    with pytest.raises(ValueError):D.monitor({},interval=interval,samples=1)


def test_actual_cycle_timer_records_ticks_without_changing_pacing(metrics,monkeypatch):
    import types
    robot_utils=types.ModuleType('lerobot.utils.robot_utils')
    robot_utils.precise_sleep=Mock()
    monkeypatch.setitem(sys.modules,'lerobot.utils.robot_utils',robot_utils)
    monkeypatch.setitem(sys.modules,'lerobot.utils.runtime_metrics',M)
    spec=importlib.util.spec_from_file_location('diagnostics_cycle_timer',ROOT/'lerobot_unified/src/lerobot/utils/cycle_timer.py')
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module);spec.loader.exec_module(module)
    timer=module.CycleTimer(30)
    for _ in range(5):timer.tick();timer.wait()
    assert metrics.snapshot()['events']['loop'][0]==5
    assert timer.fps==30 and timer.tick_interval==1/30


def test_actual_action_send_hook_records_only_after_success(metrics,monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setitem(sys.modules,'lerobot.utils.runtime_metrics',M)
    tree=ast.parse((ROOT/'lerobot_unified/src/lerobot/rollout/strategies/core.py').read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='send_next_action')
    for arg in fn.args.args:arg.annotation=None
    fn.returns=None
    ns={'os':os,'Path':Path,'contextlib':NS(nullcontext=nullcontext)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'actual_send','exec'),ns)
    robot=NS(send_action=Mock())
    ctx=NS(policy=NS(inference=NS()),data=NS(dataset_features={},ordered_action_keys=['j.pos']),
           processors=NS(robot_action_processor=lambda pair:pair[0]),hardware=NS(robot_wrapper=robot))
    interp=NS(needs_new_action=lambda:False,get=lambda:[NS(item=lambda:1.)])
    ns['send_next_action']({}, {},ctx,interp)
    assert robot.send_action.call_args.args[0]=={'j.pos':1.}
    assert metrics.snapshot()['events']['action_sent'][0]==1
    robot.send_action.side_effect=RuntimeError('original motor error')
    with pytest.raises(RuntimeError,match='original motor error'):ns['send_next_action']({}, {},ctx,interp)
    assert metrics.snapshot()['events']['action_sent'][0]==1


@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
def test_every_yaml_parameter_has_korean_comment_and_survives_save(device,tmp_path):
    import re,yaml
    source=ROOT/f'params/{device}.yaml';cfg=C.load(source)
    saved=tmp_path/'settings.yaml';C.save(saved,cfg)
    for path in (source,saved):
        text=path.read_text();lines=text.splitlines();node=yaml.compose(text)
        def check(mapping):
            if isinstance(mapping,yaml.MappingNode):
                for key,value in mapping.value:
                    previous=lines[key.start_mark.line-1].strip()
                    assert previous.startswith('#') and re.search('[가-힣]',previous),(path,key.value)
                    check(value)
        check(node)
    assert C.load(saved)==cfg

@pytest.mark.parametrize('key,value',[('enabled','yes'),('interval_s',0),('stale_s',1),('backups',0),('queue_lines',100000),('full_log_mb',float('inf'))])
def test_bad_diagnostic_config_is_rejected(key,value):
    cfg=C.load(ROOT/'params/omx1.yaml');cfg['diagnostics'][key]=value
    with pytest.raises(C.ConfigError):C.validate(cfg)

def test_edit_diagnostic_setting_and_preserve_comments(tmp_path):
    from omx_manager.cli import edit_value
    c=C.load(ROOT/'params/omx1.yaml');path=tmp_path/'settings.yaml';C.save(path,c)
    changed=edit_value(c,'diagnostics.enabled','true');C.save(path,changed)
    assert C.load(path)['diagnostics']['enabled'] is True
    assert '# 상세 성능 카운터' in path.read_text()

@pytest.mark.parametrize('field,value',[('events',[]),('timings',{'act_chunk':[1,-1,.2]}),('cameras',{'top':False}),('target_hz',float('nan'))])
def test_malformed_metrics_do_not_crash_monitor(sampler,field,value):
    s,put,p=sampler;put(10,0,0,0)
    path=D.state_dir(s.cfg)/'metrics.json';data=json.loads(path.read_text());data[field]=value;path.write_text(json.dumps(data))
    output=s.sample(10)
    assert output['metrics'] is None and output['metrics_status']=='INVALID'
    D.render(output)

def test_same_pid_reused_process_has_a_fresh_rate_window(sampler):
    s,put,p=sampler;put(10,1000,1000,1000,5,1.);s.sample(10)
    p['start_ticks']=501;put(12,0,0,0,0,0.,start=501)
    new=s.sample(12)['metrics'];assert new['loop_hz'] is None and new['act_chunk_ms'] is None
    put(14,60,40,20,1,.2,start=501)
    assert s.sample(14)['metrics']['loop_hz']==20

def test_invalid_inventory_and_stale_heartbeat_have_visible_states(sampler):
    s,put,p=sampler;s.cfg['role']='pickup'
    directory=D.state_dir(s.cfg)
    (directory/'inventory.json').write_text('{"remaining":"broken"}')
    assert s.sample(10)['phase']=='STATE_INVALID'
    (directory/'heartbeat.json').write_text('{"pid":42,"monotonic":0}')
    assert s.sample(100)['phase']=='HEARTBEAT_STALE'

def test_rotating_log_follow_continues_from_new_file(tmp_path,monkeypatch,capsys):
    path=tmp_path/'important.log';path.write_text('old\n');calls=[]
    def rotate(_):
        calls.append(1)
        if len(calls)==1:path.rename(tmp_path/'important.log.1');path.write_text('new\n')
        else:raise KeyboardInterrupt
    monkeypatch.setattr(L.time,'sleep',rotate)
    with pytest.raises(KeyboardInterrupt):L.view(path,follow=True)
    assert capsys.readouterr().out=='old\nnew\n'

def test_diagnostic_write_failure_does_not_break_counting(monkeypatch):
    from omx_manager import hooks
    cfg=C.load(ROOT/'params/omx1.yaml');cfg['home']['target']={j:0. for j in C.JOINTS};cfg['counting']['min_cycle_s']=0
    from omx_manager.workflow import registration_binding
    cfg['workflow']={'items':{'A':dict(binding=registration_binding(cfg),stages=[
        dict(name='pick',target={j:20. for j in C.JOINTS},tolerance=3.,samples=2),
        dict(name='place',target={j:40. for j in C.JOINTS},tolerance=3.,samples=2)])}}
    monkeypatch.setattr(hooks,'write_json',Mock(side_effect=OSError('disk full')))
    detector=hooks.RolloutHome(cfg,0)
    for i,value in enumerate((20.,20.,40.,40.)):assert not detector.update({j:value for j in C.JOINTS},i*.05)
    assert sum(detector.update({j:0. for j in C.JOINTS},.2+i*.05) for i in range(1,30))==1

@pytest.mark.parametrize('device',['omx1','omx2','omx3','omx4'])
def test_seeded_noisy_home_cycles_have_one_count_per_departure(device):
    import random
    from omx_manager.home import HomeDetector
    rng=random.Random(int(os.environ.get('OMX_TEST_SEED','1'))+int(device[-1]))
    cfg=C.load(ROOT/f'params/{device}.yaml');cfg['home']['target']={j:0. for j in C.JOINTS};cfg['counting']['min_cycle_s']=0
    detector=HomeDetector(cfg,hold=.5);now=0.;total=0
    for cycle in range(8):
        now+=.05;assert not detector.update({j:20. for j in C.JOINTS},now)
        count=0
        for _ in range(40):
            now+=rng.uniform(.04,.1)
            count+=detector.update({j:rng.uniform(.01,.1) for j in C.JOINTS},now)
        assert count==1;total+=count
    assert total==8


def test_launcher_device_mismatch_is_rejected_before_hardware(monkeypatch):
    hardware=Mock(side_effect=AssertionError('must not connect'))
    monkeypatch.setattr('omx_manager.cli.FollowerBus',hardware)
    with pytest.raises(ValueError,match='Device mismatch'):
        invoke(ROOT/'params/omx2.yaml','run',confirm_motion=True,expected_device='omx1')
    hardware.assert_not_called()


def test_start_wrapper_prefers_local_config_and_checks_device(tmp_path):
    import shutil
    root=tmp_path/'checkout';root.mkdir();(root/'state/config').mkdir(parents=True)
    shutil.copy2(ROOT/'run_omx_mqtt.sh',root/'run_omx_mqtt.sh')
    control=root/'omx_control';control.write_text('#!/bin/bash\nprintf \"%s\\n\" \"$@\"\n');control.chmod(0o755)
    local=root/'state/config/omx1.yaml';local.write_text('device_id: omx1')
    output=subprocess.check_output(['bash',str(root/'run_omx_mqtt.sh'),'--robot-id','omx1'],text=True)
    assert str(local) in output and '--expected-device\nomx1' in output
