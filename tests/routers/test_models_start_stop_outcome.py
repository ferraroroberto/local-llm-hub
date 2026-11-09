"""POST /api/models/{id}/start|stop — each start/stop outcome keeps its own
status, and every body carries the ``outcome`` kind beside an unchanged
``detail``, so the SPA and a peer hub's reconcile loop can tell "already in
that state" from "could not do it". ``backend_process`` is faked: no
process is spawned or stopped.
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.process import backend_process as bp  # noqa: E402
from src.process.process_supervisor import OutcomeKind, ProcessOutcome  # noqa: E402


@pytest.fixture(autouse=True)
def _example_identity(config_with_example_identity):
    """``piper`` is a tower-local tts row in the placement fixture."""


def _client() -> TestClient:
    from app_web.server import create_app

    return TestClient(create_app())


@pytest.mark.parametrize("kind, detail, status", [
    (OutcomeKind.STARTED, "started (pid=12)", 200),
    (OutcomeKind.ADOPTED, "adopted external instance (PID 3)", 200),
    (OutcomeKind.ALREADY_RUNNING, "already running", 409),
    (OutcomeKind.LAUNCH_FAILED, "failed to launch: piper binary not found", 500),
    (OutcomeKind.NOT_ENABLED, "model 'piper' not enabled on this host", 404),
    (OutcomeKind.NOT_OWNER, "model 'piper' is owned by host(s) ['mac'] — start it there", 403),
])
def test_start_maps_each_outcome_to_its_status(monkeypatch, kind, detail, status):
    monkeypatch.setattr(bp, "start", lambda mid: ProcessOutcome(kind, detail))
    r = _client().post("/api/models/piper/start")
    assert r.status_code == status
    body = r.json()
    assert body["outcome"] == kind.value
    assert body["detail"] == detail


@pytest.mark.parametrize("kind, detail, status", [
    (OutcomeKind.STOPPED, "stopped", 200),
    (OutcomeKind.NOT_RUNNING, "not running", 409),
    (OutcomeKind.STOP_FAILED, "error stopping: access denied", 500),
])
def test_stop_maps_each_outcome_to_its_status(monkeypatch, kind, detail, status):
    monkeypatch.setattr(bp, "stop", lambda mid: ProcessOutcome(kind, detail))
    r = _client().post("/api/models/piper/stop")
    assert r.status_code == status
    body = r.json()
    assert body["outcome"] == kind.value
    assert body["detail"] == detail
