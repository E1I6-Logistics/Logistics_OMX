"""Repeated Ctrl+C must not interrupt verified sensor restoration."""
import signal
import pytest
from omx_manager import exposure as E
from test_low_exposure import setup


def interrupt_during_restore(monkeypatch):
    original=E.restore_controls
    def restore(adapter,values):
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)
        original(adapter,values)
    monkeypatch.setattr(E,'restore_controls',restore)


def test_two_interrupts_restore_camera_then_honor_operator_stop(setup,monkeypatch):
    cfg,clock,controls,camera=setup
    previous=signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT,signal.default_int_handler)
    try:
        with pytest.raises(KeyboardInterrupt):
            with E.Session(camera,cfg):interrupt_during_restore(monkeypatch)
        assert controls.values==controls.initial and not E.journal_path(cfg).exists()
        assert camera._omx_exposure_blocked is False
        assert signal.getsignal(signal.SIGINT)==signal.default_int_handler
    finally:signal.signal(signal.SIGINT,previous)


def test_custom_application_stop_handler_is_called_once_after_restore(setup,monkeypatch):
    cfg,clock,controls,camera=setup;seen=[]
    previous=signal.getsignal(signal.SIGINT)
    def handler(signum,frame):seen.append((controls.values.copy(),E.journal_path(cfg).exists()))
    signal.signal(signal.SIGINT,handler)
    try:
        with E.Session(camera,cfg):interrupt_during_restore(monkeypatch)
        assert seen==[(controls.initial,False)]
        assert signal.getsignal(signal.SIGINT) is handler
    finally:signal.signal(signal.SIGINT,previous)


def test_interrupted_failed_restore_keeps_fault_and_recovery_fence(setup,monkeypatch):
    cfg,clock,controls,camera=setup
    with pytest.raises(RuntimeError,match='restoration unverified'):
        with E.Session(camera,cfg):
            controls.fail_restore=True
            interrupt_during_restore(monkeypatch)
    assert E.journal_path(cfg).exists() and camera._omx_exposure_blocked
