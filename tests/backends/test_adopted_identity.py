"""An adopted backend is identity-confirmed, and a hub never stops one it did
not spawn.

Only stubs are used: a stand-in listener (this test process itself, a python
program that is no llama-server) holds the port, the Popen handles are fakes,
and nothing here starts, stops or reaches a real backend port.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest  # noqa: E402

from src import server_lifecycle as sl  # noqa: E402
from src.model_registry import Model  # noqa: E402
from src.process import backend_process as bp  # noqa: E402
from src.process.process_supervisor import (  # noqa: E402
    OutcomeKind,
    ProcessOutcome,
    ProcessSupervisor,
)

PORT = 9124
# This test process: a live python program, so a stand-in for "some other
# program answers on the backend's port".
STRANGER_PID = os.getpid()
# A PID no process holds, so its exe cannot be read.
GONE_PID = 2**22 - 3


def _model(engine: str = "llama-server", **kw) -> Model:
    base = dict(id="m_identity", display_name="m-identity", backend="openai", engine=engine, port=PORT)
    base.update(kw)
    return Model(**base)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setitem(bp._STATES, "m_identity", bp._BackendState())


# ------------------------------------------------------ the identity check

def test_unrelated_listener_on_a_llama_port_reads_unknown_not_adopted():
    s = bp.serving(_model(), True, {PORT: [STRANGER_PID]})
    assert s.state.value == "unknown"
    assert s.reason == "port holder is not this model's server binary"
    assert s.ownership == "none"


def test_listener_whose_identity_cannot_be_read_reads_unknown():
    s = bp.serving(_model(), True, {PORT: [GONE_PID]})
    assert s.state.value == "unknown"
    assert s.reason == "port holder's identity could not be confirmed"


def test_confirmed_backend_binary_is_adopted():
    # A python-hosted engine's binary is python, which the stand-in is.
    s = bp.serving(_model(engine="tts-server"), True, {PORT: [STRANGER_PID]})
    assert (s.state.value, s.ownership, s.pid) == ("adopted", "external", STRANGER_PID)


def test_holder_confirmed_is_three_valued():
    assert bp.holder_confirmed(_model(), None) is None
    assert bp.holder_confirmed(_model(), GONE_PID) is None
    assert bp.holder_confirmed(_model(), STRANGER_PID) is False
    assert bp.holder_confirmed(_model(engine="tts-server"), STRANGER_PID) is True


def test_python_exe_is_not_a_llama_server():
    assert bp._looks_like_backend_binary("C:/py/python.exe", _model()) is False
    assert bp._looks_like_backend_binary("C:/vendor/llama-server.exe", _model()) is True


# ------------------------------------------------------------------- start

def _no_spawn(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("start must not spawn over a listener")

    monkeypatch.setattr(subprocess, "Popen", _boom)


def test_start_declines_an_unconfirmed_listener_and_neither_adopts_nor_spawns(monkeypatch):
    model = _model()
    monkeypatch.setattr(bp, "resolve_model_by_id", lambda _id: model)
    monkeypatch.setattr(bp, "is_reachable", lambda *_a, **_k: True)
    monkeypatch.setattr(bp, "external_pid", lambda _id: STRANGER_PID)
    _no_spawn(monkeypatch)

    outcome = bp.start("m_identity")

    assert outcome.kind is OutcomeKind.UNCONFIRMED_LISTENER
    assert outcome.ok is False and outcome.converged is False
    assert outcome.pid == STRANGER_PID


def test_start_adopts_a_confirmed_listener(monkeypatch):
    model = _model(engine="tts-server")
    monkeypatch.setattr(bp, "resolve_model_by_id", lambda _id: model)
    monkeypatch.setattr(bp, "is_reachable", lambda *_a, **_k: True)
    monkeypatch.setattr(bp, "external_pid", lambda _id: STRANGER_PID)
    _no_spawn(monkeypatch)

    assert bp.start("m_identity").kind is OutcomeKind.ADOPTED


def _supervisor(confirm):
    return ProcessSupervisor(
        already_running=lambda: False,
        reachable=lambda: True,
        external_pid=lambda: 7,
        build_spawn_spec=lambda: pytest.fail("must not spawn"),
        set_process=lambda _p: None,
        adopt_message="adopted",
        listener_confirmed=confirm,
    )


def test_supervisor_without_a_check_still_adopts_any_reachable_listener():
    assert _supervisor(None).start().kind is OutcomeKind.ADOPTED


@pytest.mark.parametrize("verdict", [False, None])
def test_supervisor_declines_a_listener_the_check_does_not_confirm(verdict):
    assert _supervisor(lambda _pid: verdict).start().kind is OutcomeKind.UNCONFIRMED_LISTENER


# ---------------------------------------------------------------- shutdown

class _FakePopen:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self):
        return None


def test_shutdown_stops_only_what_the_hub_spawned(monkeypatch):
    spawned = _model(id="m_spawned", port=PORT + 1)
    inherited = _model(id="m_inherited", port=PORT + 2)
    adopted = _model(id="m_adopted", port=PORT + 3)
    monkeypatch.setattr(bp, "local_models", lambda: [spawned, inherited, adopted])

    own = bp._BackendState()
    own.proc = _FakePopen(11)
    kept = bp._BackendState()
    kept.inherited_pid = STRANGER_PID  # alive, but not spawned by this hub
    monkeypatch.setitem(bp._STATES, "m_spawned", own)
    monkeypatch.setitem(bp._STATES, "m_inherited", kept)
    monkeypatch.setitem(bp._STATES, "m_adopted", bp._BackendState())  # a listener only

    assert set(bp.running_backends()) == {"m_spawned", "m_inherited"}
    assert set(bp.spawned_backends()) == {"m_spawned"}

    stopped: list[str] = []
    monkeypatch.setattr(bp, "restart_pending", lambda: False)
    monkeypatch.setattr(
        bp, "stop", lambda mid: stopped.append(mid) or ProcessOutcome(OutcomeKind.STOPPED, "stopped"),
    )

    asyncio.run(sl.stop_backend_children())

    assert stopped == ["m_spawned"]
