"""Real motion-cycle controller/IPC with fake clock, robot and MQTT transport."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from omx_manager import workstation as W,workstation_cycles as CW,basket,commissioning as C,orders,runtime
from omx_manager.cli import edit_value
from test_workflow_recovery import cfg,controller
from test_shell_shortcuts import layout

ROOT=Path(__file__).resolve().parents[1]


def prepare(cfg,tmp_path,monkeypatch,**kwargs):
    cfg['unload']['inspection_enabled']=False
    cfg['unload']['reference']='/missing/empty-reference.npz'
    run,phases,sent,done,path=controller(cfg,tmp_path,monkeypatch,**kwargs)
    monkeypatch.setattr(CW,'time',W.time);monkeypatch.setattr(CW,'initialize_home',W.initialize_home)
    monkeypatch.setattr(CW,'heartbeat',lambda:None)
    monkeypatch.setattr(W,'BasketInspector',Mock(side_effect=AssertionError('Must not load basket reference')))
    from omx_manager import exposure
    monkeypatch.setattr(exposure,'Session',Mock(side_effect=AssertionError('Must not change exposure')))
    return run,phases,sent,done,path


@pytest.mark.parametrize('device',['omx3','omx4'])
@pytest.mark.parametrize('count',[1,2,4,8])
def test_exact_requested_motion_cycles_with_no_basket_or_exposure_calls(cfg,tmp_path,monkeypatch,count,device):
    cfg['device_id']=device
    run,phases,sent,done,path=prepare(cfg,tmp_path,monkeypatch,expected=count)
    run()
    assert sent and len(done)==1
    row=done[0]
    assert row['completed_cycles']==row['requested_cycles']==count
    assert row['reason']=='requested_cycles_completed' and row['inspection_scope']=='disabled'
    assert row['count_kind']=='motion_cycles' and row['estimated_transfers'] is None
    assert row['basket_empty_verified'] is False and row['physical_unloading_verified'] is False
    assert 'INSPECTING' not in phases


def test_second_order_resets_cycle_count_and_reuses_controller(cfg,tmp_path,monkeypatch):
    run,phases,sent,done,path=prepare(cfg,tmp_path,monkeypatch,expected=2,jobs=2)
    run()
    assert len(done)==2 and [row['completed_cycles'] for row in done]==[2,2]
    assert done[0]['job_id']!=done[1]['job_id']


@pytest.mark.parametrize('value',[None,0,True,-1,9,1.5,'2'])
def test_missing_or_invalid_count_performs_no_actions(cfg,tmp_path,monkeypatch,value):
    run,phases,sent,done,path=prepare(cfg,tmp_path,monkeypatch,expected=value)
    run()
    assert not sent and not done and 'JOB_FAILED' in phases
    assert json.loads(path.read_text())['completed_cycles']==0


def test_incomplete_movement_times_out_without_counting(cfg,tmp_path,monkeypatch):
    cfg['runtime']['cycle_timeout_s']=2
    run,phases,sent,done,path=prepare(cfg,tmp_path,monkeypatch,expected=2,stage_failure=True)
    with pytest.raises(TimeoutError,match='departure/Home return'):run()
    assert not done and json.loads(path.read_text())['completed_cycles']==0
    assert phases[-1]=='ERROR'


def test_off_switch_preserves_saved_baseline_and_home(cfg):
    new=edit_value(cfg,'unload.inspection_enabled','false')
    assert not new['unload']['inspection_enabled']
    assert new['home']==cfg['home'] and new['models']==cfg['models']
    assert new['unload']['reference']==cfg['unload']['reference']
    assert edit_value(new,'unload.inspection_enabled','true')['unload']['inspection_enabled']
    with pytest.raises(ValueError):edit_value(cfg,'unload.inspection_enabled','0')


def test_until_empty_cli_is_rejected_before_any_mqtt_publish(cfg,monkeypatch):
    cfg['unload']['inspection_enabled']=False
    monkeypatch.setattr(orders.subprocess,'run',Mock(side_effect=AssertionError('No publish')))
    with pytest.raises(ValueError,match='omx_unload 1'):orders.send_order(cfg,until_empty=True)


def test_runtime_advertises_cycle_mode_and_disabled_scope(cfg):
    cfg['unload']['inspection_enabled']=False
    _,_,env,_=runtime.build(cfg,ROOT/'params/omx4.yaml')
    assert env['OMX_UNLOAD_MODE']=='cycles' and env['OMX_UNLOAD_VIEW_SCOPE']=='disabled'


def test_commissioning_without_inspection_does_not_load_a_reference(cfg,monkeypatch):
    cfg['unload']['inspection_enabled']=False;cfg['unload']['reference']=None
    monkeypatch.setattr(C,'require_home',lambda *_:None)
    monkeypatch.setattr(basket,'BasketInspector',Mock(side_effect=AssertionError('No image reference needed')))
    C.require_commissioned(cfg)


def worker(monkeypatch,rows):
    import os
    from omx_manager.simulation import Clock
    monkeypatch.setenv('OMX_MANAGED_UNLOAD','1');monkeypatch.setenv('OMX_UNLOAD_MODE','cycles')
    monkeypatch.setenv('OMX_UNLOAD_VIEW_SCOPE','disabled')
    tree=ast.parse((ROOT/'lerobot_unified/omx_mqtt_client.py').read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('execute_unload','send_result')]
    messages=[];commands=[];clock=Clock();queue=iter(rows)
    env=dict(robot_id='omx3',WORKSTATION_IDS=('omx3','omx4'),os=os,
             workstation_session='s',workstation_command=Path('/unused/command'),
             workstation_ready=lambda:True,workstation_state=lambda:next(queue),
             _workstation_process_alive=lambda *_:True,
             atomic_json=lambda p,row:commands.append(row),publish=lambda topic,row:messages.append((topic,row)),
             time=NS(monotonic=clock,time=lambda:1000+clock(),sleep=clock.sleep))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'<MQTT cycle worker>','exec'),env)
    return env,messages,commands


def completion(**extra):
    return dict(session='s',phase='DONE',job_id='job',reason='requested_cycles_completed',
                completed_cycles=2,requested_cycles=2,inspection_scope='disabled',count_kind='motion_cycles',
                physical_unloading_verified=False,basket_empty_verified=False,**extra)


def test_mqtt_accepts_cycle_evidence_without_claiming_empty(monkeypatch):
    env,messages,commands=worker(monkeypatch,[completion()])
    row=env['execute_unload']('job',{'E':2})
    assert row['completed_cycles']==2 and [r['command'] for r in commands]==['START','CLEAR']
    progress=messages[-1][1]
    assert progress['current']==progress['total']==2 and progress['count_kind']=='motion_cycles'
    env['send_result']('job',True,'Motion cycles completed')
    payload=messages[-1][1]
    assert payload['basket_empty_verified'] is False and payload['physical_unloading_verified'] is False
    assert payload['inspection_scope']=='disabled'


@pytest.mark.parametrize('key,value',[('completed_cycles',1),('requested_cycles',3),('reason','basket_empty_confirmed'),
                                     ('basket_empty_verified',True),('physical_unloading_verified',True),('count_kind','estimated_transfers')])
def test_mqtt_rejects_wrong_count_or_fabricated_completion(monkeypatch,key,value):
    row=completion();row[key]=value
    env,messages,commands=worker(monkeypatch,[row])
    with pytest.raises(RuntimeError):env['execute_unload']('job',{'E':2})
    assert [r['command'] for r in commands]==['START']


def test_mqtt_rejects_unknown_quantity_before_start_command(monkeypatch):
    env,messages,commands=worker(monkeypatch,[])
    with pytest.raises(ValueError,match='1..8'):env['execute_unload']('job',{})
    assert not commands


def test_bash_switches_use_existing_config_writer(layout):
    from test_shell_shortcuts import register_shortcuts,shell
    register_shortcuts(layout,'omx3')
    for switch,expected in [('off','false'),('on','true')]:
        result=shell(layout,'omx_basket '+switch)
        assert result.returncode==0
        args=json.loads(result.stdout)['args']
        assert args[-4:]==['--key','unload.inspection_enabled','--value',expected]


@pytest.mark.parametrize('device',['omx3','omx4'])
def test_learned_cycle_counter_completes_consecutive_orders_without_images(tmp_path,monkeypatch,device):
    from test_cycle_counting import trained
    cfg=trained(device,wrist=0)
    run,phases,sent,done,path=prepare(cfg,tmp_path,monkeypatch,expected=2,jobs=2)
    run()
    assert len(done)==2 and [row['completed_cycles'] for row in done]==[2,2]
    assert all(row['physical_unloading_verified'] is False for row in done)


def test_precision_home_failure_cannot_count_a_completed_cycle(cfg,tmp_path,monkeypatch):
    run,phases,sent,done,path=prepare(cfg,tmp_path,monkeypatch,expected=1)
    calls=0;home=CW.initialize_home
    def fail_second(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:raise TimeoutError('Home correction did not arrive')
        return home(*args,**kwargs)
    monkeypatch.setattr(CW,'initialize_home',fail_second)
    with pytest.raises(TimeoutError,match='Home correction'):run()
    assert not done and json.loads(path.read_text())['completed_cycles']==0
