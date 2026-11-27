"""``backend_process.stop``: by PID, and a survivor stays the hub's.

Nothing spawns a process: the ``Popen`` handle, ``psutil`` and ``kill_pid``
are faked.
"""

from __future__ import annotations

import logging
import sys

import pytest

from src.process import backend_process as bp
from src.process.process_supervisor import OutcomeKind
from src.process.serving_state import LoadRecord

MODEL_ID = "stop-test-model"


class _FakePopen:
    pid = 4321

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.returncode = None

    def poll(self):
        return self.returncode

    def send_signal(self, _sig) -> None:
        # CPython 3.12.3 / 3.13.0 from a console-less hub (the pythonw tray).
        self.calls.append("send_signal")
        raise SystemError("<built-in function kill> returned a result with an exception set")

    def terminate(self) -> None:
        self.calls.append("terminate")
        self.returncode = 1

    def wait(self, timeout: float = 0):
        return self.returncode


@pytest.fixture
def state(monkeypatch) -> "bp._BackendState":
    fresh = bp._BackendState()
    fresh.load = LoadRecord(started_at=100.0, ready=True)
    monkeypatch.setitem(bp._STATES, MODEL_ID, fresh)
    return fresh


def test_stop_sends_no_console_event(monkeypatch, state):
    monkeypatch.setattr(sys, "platform", "win32")
    state.proc = proc = _FakePopen()

    outcome = bp.stop(MODEL_ID)

    assert outcome.kind is OutcomeKind.STOPPED, outcome.detail
    assert proc.calls == ["terminate"]
    assert state.proc is None and state.load is None


def test_a_failed_stop_logs_the_backend_and_pid(state, caplog):
    class Unkillable(_FakePopen):
        def terminate(self) -> None:
            raise PermissionError("access is denied")

    state.proc = proc = Unkillable()

    with caplog.at_level(logging.WARNING, logger=bp.__name__):
        outcome = bp.stop(MODEL_ID)

    assert outcome.kind is OutcomeKind.STOP_FAILED
    assert MODEL_ID in caplog.text and "4321" in caplog.text
    assert state.proc is proc and state.load is not None


def test_a_failed_inherited_kill_keeps_the_pid_and_logs_it(monkeypatch, state, caplog):
    state.inherited_pid = 4242
    monkeypatch.setattr(bp, "_inherited_alive", lambda s: s.inherited_pid == 4242)
    monkeypatch.setattr(bp, "kill_pid", lambda _pid: (False, "access is denied"))

    with caplog.at_level(logging.WARNING, logger=bp.__name__):
        outcome = bp.stop(MODEL_ID)

    assert outcome.kind is OutcomeKind.STOP_FAILED
    assert MODEL_ID in caplog.text and "4242" in caplog.text
    assert state.inherited_pid == 4242 and state.load is not None


def test_a_killed_inherited_backend_is_forgotten(monkeypatch, state):
    state.inherited_pid = 4242
    state.inherited_create_time = 1.0
    monkeypatch.setattr(bp, "_inherited_alive", lambda s: s.inherited_pid == 4242)
    monkeypatch.setattr(bp, "kill_pid", lambda pid: (True, f"killed pid {pid}"))

    outcome = bp.stop(MODEL_ID)

    assert outcome.kind is OutcomeKind.STOPPED
    assert state.inherited_pid is None and state.inherited_create_time is None
    assert state.load is None
