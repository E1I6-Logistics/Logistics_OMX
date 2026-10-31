import json
from omx_manager.basket_console import WatchOutput
from omx_manager.simulation import Clock


def row(verdict='UNKNOWN',reason='TEMPORAL_WARMUP',**kw):
    return dict(verdict=verdict,candidate='EMPTY',reason=reason,processing_ms=10.6,
                frame_age_ms=27.5,at=1793412816.,**kw)


def test_default_watch_is_one_short_line_without_internal_evidence():
    clock=Clock();lines=[]
    out=WatchOutput('omx4',clock=clock,emit=lines.append)
    out.update(row('OCCUPIED','CONFIRMED',tray_quad=[[1,2]],component_pixels=35))
    assert len(lines)==1 and '물건 있음' in lines[0] and '10.6ms' in lines[0]
    assert 'tray_quad' not in lines[0] and 'component_pixels' not in lines[0]


def test_watch_ignores_confirmation_churn_but_reports_verdict_changes_immediately():
    clock=Clock();lines=[];out=WatchOutput('omx3',clock=clock,emit=lines.append)
    assert out.update(row())
    clock.sleep(.1);assert not out.update(row(reason='STABILIZING'))
    clock.sleep(.1);assert out.update(row('EMPTY','CONFIRMED'))
    clock.sleep(.1);assert out.update(row('OCCUPIED','CONFIRMED'))
    clock.sleep(4.9);assert not out.update(row('OCCUPIED','CONFIRMED'))
    clock.sleep(.1);assert out.update(row('OCCUPIED','CONFIRMED'))
    assert len(lines)==4


def test_lighting_value_changes_do_not_flood_watch():
    clock=Clock();lines=[];out=WatchOutput('omx4',clock=clock,emit=lines.append)
    out.update(row(reason='LIGHTING_SHIFT',illumination_delta=17.))
    clock.sleep(.1);out.update(row(reason='LIGHTING_SHIFT',illumination_delta=18.))
    assert len(lines)==1 and '조명 차이 17' in lines[0]


def test_json_keeps_full_diagnostics_and_saved_rows_remain_unmodified():
    clock=Clock();lines=[];saved=[];out=WatchOutput('omx4',json_output=True,clock=clock,emit=lines.append,save=saved.append)
    sample=row(component_pixels=35,quality_flags=['LIGHTING_SHIFT'])
    before=dict(sample);out.update(sample)
    assert json.loads(lines[0])==before and saved==[before] and sample==before
    clock.sleep(.1);out.update(sample);assert len(saved)==1
    clock.sleep(.9);out.update(sample);assert len(saved)==2


def test_disk_failure_does_not_stop_watching_or_repeat_warning():
    def fail(_):raise OSError('disk unavailable')
    clock=Clock();lines=[];out=WatchOutput('omx4',clock=clock,emit=lines.append,save=fail)
    out.update(row());clock.sleep(5);out.update(row())
    assert sum('진단 저장 실패' in x for x in lines)==1
    assert sum('보류' in x for x in lines)==2
