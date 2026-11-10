"""A model's serving state is derived once and read the same way everywhere.

Pins each state (stopped, loading, ready, adopted, failed, unknown), the
transitions between them, and the readers that used to fold loading, failed
and unknown into "idle", "running" or "stopped": the admin models payload,
the audio health verdict and the fleet placement summary. Nothing spawns a
process — the Popen handle is a stub.
"""

from __future__ import annotations

import asyncio
import os
import time

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app_web.routers import fleet_placement as fpr  # noqa: E402
from app_web.routers import models as models_router  # noqa: E402
from src import server as server_mod  # noqa: E402
from src.model_registry import STARTUP_ON_DEMAND, Model  # noqa: E402
from src.process import backend_process as bp  # noqa: E402
from src.process.serving_state import LOAD_DEADLINE_S, LoadRecord  # noqa: E402

PORT = 9123


class _FakePopen:
    """Stand-in for the hub's Popen handle: alive until ``exit`` is called."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode

    def exit(self, code: int) -> None:
        self.returncode = code


def _model(**kw) -> Model:
    base = dict(
        id="m_serving", display_name="m-serving", backend="openai",
        engine="llama-server", port=PORT,
    )
    base.update(kw)
    return Model(**base)


@pytest.fixture
def spawned(monkeypatch):
    """A model the hub just spawned, not yet answering."""
    state = bp._BackendState()
    state.proc = _FakePopen()
    state.load = LoadRecord(started_at=time.monotonic())
    monkeypatch.setitem(bp._STATES, "m_serving", state)
    return state


def _serving(model, reachable, listening=None, now=None):
    listening = {PORT: [4242]} if listening is None else listening
    return bp.serving(model, reachable, listening, now=now)


# --------------------------------------------------------------- the states

def test_spawned_and_not_yet_answering_is_loading(spawned):
    s = _serving(_model(), reachable=False)
    assert (s.state.value, s.ownership, s.pid) == ("loading", "ours", 4242)


def test_first_good_probe_makes_it_ready(spawned):
    assert _serving(_model(), reachable=True).state.value == "ready"
    assert spawned.load.ready is True


def test_missing_the_load_deadline_is_failed_with_a_reason(spawned):
    s = _serving(_model(), reachable=False, now=spawned.load.started_at + LOAD_DEADLINE_S + 1)
    assert s.state.value == "failed"
    assert "did not answer within" in s.reason


def test_a_slow_load_that_answers_after_the_deadline_recovers_to_ready(spawned):
    late = spawned.load.started_at + LOAD_DEADLINE_S + 30
    assert _serving(_model(), reachable=False, now=late).state.value == "failed"
    assert _serving(_model(), reachable=True, now=late).state.value == "ready"


def test_dying_during_load_is_failed_not_stopped(spawned):
    spawned.proc.exit(3)
    s = _serving(_model(), reachable=False, listening={})
    assert s.state.value == "failed"
    assert s.reason == "process exited during load (exit code 3)"
    assert s.ownership == "none"


def test_dying_after_ready_is_failed(spawned):
    _serving(_model(), reachable=True)
    spawned.proc.exit(1)
    s = _serving(_model(), reachable=False, listening={})
    assert (s.state.value, s.reason) == ("failed", "process exited after it was ready (exit code 1)")


def test_alive_but_no_longer_answering_is_failed(spawned):
    _serving(_model(), reachable=True)
    s = _serving(_model(), reachable=False)
    assert (s.state.value, s.ownership) == ("failed", "ours")
    assert s.reason == "process is alive but stopped answering"


def test_stopping_a_dead_process_acknowledges_the_failure(spawned):
    spawned.proc.exit(3)
    bp.stop("m_serving")
    assert _serving(_model(), reachable=False, listening={}).state.value == "stopped"


def test_never_started_with_a_free_port_is_stopped(monkeypatch):
    monkeypatch.setitem(bp._STATES, "m_serving", bp._BackendState())
    s = _serving(_model(), reachable=False, listening={})
    assert (s.state.value, s.ownership, s.pid) == ("stopped", "none", None)


def test_someone_elses_listener_is_adopted(monkeypatch):
    monkeypatch.setitem(bp._STATES, "m_serving", bp._BackendState())
    monkeypatch.setattr(bp, "holder_confirmed", lambda _model, _pid: True)
    s = _serving(_model(), reachable=True, listening={PORT: [5151]})
    assert (s.state.value, s.ownership, s.pid) == ("adopted", "external", 5151)


def test_a_failed_port_snapshot_is_unknown_not_stopped(monkeypatch):
    monkeypatch.setitem(bp._STATES, "m_serving", bp._BackendState())
    s = bp.serving(_model(), False, None)
    assert s.state.value == "unknown"
    assert s.ownership == "none"  # the value older clients read
    assert s.reason == "listening-port snapshot failed"


def _inherit_one(monkeypatch, exe: str) -> None:
    """Run hub-startup inheritance over one llama-server listening on PORT."""
    import psutil

    monkeypatch.setattr(bp, "local_models", lambda: [_model()])
    monkeypatch.setitem(bp._STATES, "m_serving", bp._BackendState())

    class _Proc:
        def exe(self):
            return exe

        def cmdline(self):
            return [exe]

        def cwd(self):
            return exe.rsplit("/", 1)[0]

        def create_time(self):
            return 1.0

        def status(self):
            return psutil.STATUS_RUNNING

    monkeypatch.setattr(psutil, "Process", lambda _pid: _Proc())
    monkeypatch.setattr(psutil, "pid_exists", lambda _pid: True)
    monkeypatch.setattr(
        "src.process.server_process.snapshot_listening_pids", lambda: {PORT: [777]})
    assert bp.inherit_running_backends() == 1


def test_an_inherited_backend_loads_until_it_answers(monkeypatch):
    _inherit_one(monkeypatch, str(bp.VENDOR_LLAMA / "llama-server.exe").replace("\\", "/"))
    assert bp.serving(_model(), False, {PORT: [777]}).state.value == "loading"
    assert bp.serving(_model(), True, {PORT: [777]}).state.value == "ready"


def test_a_foreign_inherited_server_exiting_is_not_a_failure(monkeypatch):
    """A sibling app's server on a shared port is not the hub's to fail."""
    _inherit_one(monkeypatch, "C:/elsewhere/llama-server.exe")
    assert bp.serving(_model(), True, {PORT: [777]}).state.value == "ready"
    import psutil

    monkeypatch.setattr(psutil, "pid_exists", lambda _pid: False)
    assert bp.serving(_model(), False, {}).state.value == "stopped"


def test_on_demand_readiness_latches_ready(spawned):
    bp.mark_ready("m_serving")
    assert spawned.load.ready is True


# --------------------------------------------------------------- the readers

def test_admin_row_carries_the_serving_state(spawned):
    row = models_router._local_row(_model(), False, None, {PORT: [4242]}, "tower", {})
    assert row["serving_state"] == "loading"
    assert (row["ownership"], row["pid"]) == ("ours", 4242)


def test_admin_row_reports_unknown_when_the_snapshot_failed(monkeypatch):
    monkeypatch.setitem(bp._STATES, "m_serving", bp._BackendState())
    row = models_router._local_row(_model(), False, None, None, "tower", {})
    assert row["serving_state"] == "unknown"
    assert row["serving_reason"] == "listening-port snapshot failed"


def test_admin_row_reports_failed_with_its_reason(spawned):
    spawned.proc.exit(3)
    row = models_router._local_row(_model(), False, None, {}, "tower", {})
    assert row["serving_state"] == "failed"
    assert row["serving_reason"] == "process exited during load (exit code 3)"


def _audio_row(**kw) -> Model:
    return _model(backend="whisper", engine="whisper-server", startup=STARTUP_ON_DEMAND, **kw)


def test_audio_health_counts_an_on_demand_row_that_died_loading_as_down(monkeypatch, spawned):
    spawned.proc.exit(3)
    monkeypatch.setattr("src.model_registry.local_models", lambda: [_audio_row()])
    monkeypatch.setattr(bp, "is_reachable", lambda m, timeout=1.0: False)
    monkeypatch.setattr("src.process.server_process.snapshot_listening_pids", lambda: {})

    resp = TestClient(server_mod.app).get("/v1/audio/health")

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["backends"][0]["state"] == "failed"
    assert body["backends"][0]["reason"] == "process exited during load (exit code 3)"


def test_audio_health_reports_a_loading_row_without_calling_it_an_outage(monkeypatch, spawned):
    monkeypatch.setattr("src.model_registry.local_models", lambda: [_audio_row()])
    monkeypatch.setattr(bp, "is_reachable", lambda m, timeout=1.0: False)
    monkeypatch.setattr(
        "src.process.server_process.snapshot_listening_pids", lambda: {PORT: [4242]})

    resp = TestClient(server_mod.app).get("/v1/audio/health")

    assert resp.status_code == 200, resp.text
    assert resp.json()["backends"][0]["state"] == "loading"


def test_fleet_placement_does_not_count_a_loading_backend_as_running(monkeypatch, spawned):
    ready = _model(id="m_ready", port=PORT + 1)
    ready_state = bp._BackendState()
    ready_state.proc = _FakePopen(pid=99)
    monkeypatch.setitem(bp._STATES, "m_ready", ready_state)
    monkeypatch.setattr(fpr, "snapshot_listening_pids", lambda: {PORT: [4242], PORT + 1: [99]})
    monkeypatch.setattr(bp, "is_reachable", lambda m, timeout=0.4: m.id == "m_ready")

    ids = asyncio.run(fpr._serving_ids({"m_serving": _model(), "m_ready": ready}))

    assert ids == ["m_ready"]
