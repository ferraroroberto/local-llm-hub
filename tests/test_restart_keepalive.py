"""Regression: a hub restart must leave model backends alive for adoption.

Issue #44 item 3. The shutdown handler used to tear down every spawned
backend unconditionally. On ``/admin/api/hub/restart`` that killed the very
processes ``inherit_running_backends()`` exists to re-adopt, so whisper /
qwen came back DOWN. The fix: skip teardown while a restart is in flight,
flagged via ``backend_process.set_restart_pending()``; the respawned hub
then adopts the survivors.
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

import asyncio

import pytest

from src import backend_process as bp
from src import server as server_mod


@pytest.fixture(autouse=True)
def _reset_restart_flag():
    bp.set_restart_pending(False)
    yield
    bp.set_restart_pending(False)


def _patch_backends(monkeypatch):
    """Pretend two backends are running and record any stop() calls."""
    stopped: list[str] = []
    monkeypatch.setattr(bp, "running_backends", lambda: {"qwen": object(), "whisper": object()})
    monkeypatch.setattr(bp, "stop", lambda mid: (stopped.append(mid), (True, "stopped"))[1])
    return stopped


def test_restart_pending_defaults_false():
    assert bp.restart_pending() is False


def test_shutdown_tears_down_backends_normally(monkeypatch):
    stopped = _patch_backends(monkeypatch)

    asyncio.run(server_mod._stop_backend_children())

    assert sorted(stopped) == ["qwen", "whisper"]


def test_shutdown_skips_teardown_during_restart(monkeypatch):
    stopped = _patch_backends(monkeypatch)

    bp.set_restart_pending(True)
    asyncio.run(server_mod._stop_backend_children())

    # Survivors are left alive for the respawned hub to adopt.
    assert stopped == []
