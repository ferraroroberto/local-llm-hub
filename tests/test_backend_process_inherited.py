"""An inherited backend's PID is only trusted while its create-time matches.

Windows reuses PIDs aggressively: if an inherited backend dies and an
unrelated process takes its PID, ``_inherited_alive`` must report it gone so
``stop()`` (idle unload, failover, admin Stop) never kills the stranger.
Nothing spawns a process — ``psutil`` is stubbed.
"""

from __future__ import annotations

import psutil

from src import backend_process as bp


class _FakeProc:
    def __init__(self, create_time: float, status: str = psutil.STATUS_RUNNING):
        self._create_time = create_time
        self._status = status

    def create_time(self) -> float:
        return self._create_time

    def status(self) -> str:
        return self._status


def _inherit(pid: int, create_time) -> "bp._BackendState":
    state = bp._BackendState()
    state.inherited_pid = pid
    state.inherited_create_time = create_time
    return state


def _stub_psutil(monkeypatch, proc: _FakeProc) -> None:
    monkeypatch.setattr(psutil, "pid_exists", lambda _pid: True)
    monkeypatch.setattr(psutil, "Process", lambda _pid: proc)


def test_same_create_time_is_still_the_inherited_backend(monkeypatch):
    _stub_psutil(monkeypatch, _FakeProc(create_time=1000.0))
    state = _inherit(4242, 1000.0)

    assert bp._inherited_alive(state) is True
    assert state.inherited_pid == 4242


def test_reused_pid_is_dropped_instead_of_adopted(monkeypatch):
    _stub_psutil(monkeypatch, _FakeProc(create_time=2000.0))
    state = _inherit(4242, 1000.0)

    assert bp._inherited_alive(state) is False
    assert state.inherited_pid is None
    assert state.inherited_create_time is None


def test_unreadable_create_time_at_adoption_falls_back_to_liveness(monkeypatch):
    """No recorded create-time means the reuse check can't run; the pid/status
    checks still apply."""
    _stub_psutil(monkeypatch, _FakeProc(create_time=2000.0))
    state = _inherit(4242, None)

    assert bp._inherited_alive(state) is True


def test_zombie_inherited_process_is_not_alive(monkeypatch):
    _stub_psutil(monkeypatch, _FakeProc(1000.0, status=psutil.STATUS_ZOMBIE))
    state = _inherit(4242, 1000.0)

    assert bp._inherited_alive(state) is False
    assert state.inherited_pid is None
