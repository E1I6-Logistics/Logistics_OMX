"""Positive full-view color evidence and passive dispatch diagnostics."""
import json
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest
from omx_manager import basket_registration as R
from test_basket_registration import spec
from test_diagnostics import M


@pytest.mark.parametrize('offset',[[-4,-10,-17],[-13,-17,-27]])
def test_full_view_colored_cargo_survives_global_rgb_drift(offset):
    s=spec();ref=np.full((128,128,3),130,np.uint8)
    image=(ref.astype(np.int16)+offset).clip(0,255).astype(np.uint8)
    image[50:66,50:66]=[100,70,40]
    r=R.Comparator(ref,s).compare(image)
    assert r['verdict']=='OCCUPIED' and r['evidence_reason']=='REGISTERED_COLORED_OBJECT'
    assert r['quality_warnings']==['LIGHTING_SHIFT'] and not r['quality_flags']
    assert r['view_scope']=='full' and r['full_basket_verified'] is False


@pytest.mark.parametrize('gradient',[False,True])
def test_full_view_global_color_drift_and_noise_never_authorize_motion(gradient):
    s=spec();line=np.linspace(65,170,128).astype(np.uint8) if gradient else np.full(128,130,np.uint8)
    ref=np.repeat(np.tile(line,(128,1))[:,:,None],3,axis=2)
    rng=np.random.default_rng(42)
    for _ in range(10):
        image=(ref.astype(np.int16)+[-4,-10,-17]+rng.integers(-1,2,ref.shape)).clip(0,255).astype(np.uint8)
        r=R.Comparator(ref,s).compare(image)
        assert r['verdict']=='UNKNOWN'
        assert r['chroma_component_pixels']==0


@pytest.mark.parametrize('gate',['full_view_confirmed','dark','clipped'])
def test_full_view_color_cannot_override_other_quality_failures(gate):
    s=spec();ref=np.full((128,128,3),130,np.uint8)
    image=ref-np.array([4,10,17],np.uint8);image[50:70,50:70]=[100,70,40]
    if gate=='full_view_confirmed':s['full_view_confirmed']=False
    else:image[:]=0 if gate=='dark' else 255
    r=R.Comparator(ref,s).compare(image)
    assert r['verdict']=='UNKNOWN' and 'chroma_component_pixels' not in r


def test_full_view_new_default_preserves_reference_signature():
    from omx_manager.rgb_roi import signature
    s=spec();old=dict(s);old.pop('registration_full_chroma_fallback')
    # A configured default and omitted default represent the same saved mode.
    from omx_manager import basket
    assert signature(s)==signature(basket.options({'unload':old}))


def test_passive_goals_distinguish_small_policy_output_from_motor_tracking(monkeypatch):
    c=M.Counters();monkeypatch.setattr(M,'COUNTERS',c);monkeypatch.setattr(M,'ENABLED',True)
    for value in range(250):
        M.action_diagnostic({'elbow.pos':value+10.},{'elbow.pos':value+5.},
                            {'elbow.pos':float(value)},{'elbow.pos':0.})
    result=c.snapshot()['actions']
    assert result['window_samples']==200 and result['sent_span']['elbow.pos']==199
    assert result['observed_span']['elbow.pos']==0
    assert result['predicted']['elbow.pos']==259. and result['sent']['elbow.pos']==249.
    assert result['processed']['elbow.pos']==254.
    assert result['observed_before_inference']['elbow.pos']==0


def test_disabled_or_partial_diagnostics_do_not_affect_control(monkeypatch):
    c=M.Counters();monkeypatch.setattr(M,'COUNTERS',c);monkeypatch.setattr(M,'ENABLED',False)
    M.action_diagnostic(None,None,None,None)
    assert 'actions' not in c.snapshot()
    monkeypatch.setattr(M,'ENABLED',True)
    M.action_diagnostic({}, {}, {'elbow.pos':float('nan')},{})
    M.action_diagnostic(None,None,None,None)
    assert 'actions' not in c.snapshot()


def test_policy_metadata_uses_existing_writer_without_polling_hardware(tmp_path,monkeypatch):
    path=tmp_path/'metrics.json';monkeypatch.setenv('OMX_METRICS_PATH',str(path))
    monkeypatch.setattr(M,'ENABLED',True);monkeypatch.setattr(M,'COUNTERS',M.Counters())
    policy=NS(config=NS(type='act',chunk_size=100,n_action_steps=100,temporal_ensemble_coeff=None,
                       input_features={'observation.images.top':object()}))
    robot=NS(cameras={});stop=NS(is_set=lambda:False)
    M.start_metrics(robot,stop,30,interval=.01,policy=policy,policy_path='/models/E/250000')
    try:
        deadline=time.monotonic()+2
        while not path.exists() and time.monotonic()<deadline:time.sleep(.01)
        row=json.loads(path.read_text())
        assert row['policy']['initial_path']=='/models/E/250000'
        assert row['policy']['n_action_steps']==100
        assert row['policy']['input_features']==['observation.images.top']
    finally:robot._omx_metrics_close()
