"""Regression: a hub restart must leave model backends alive for adoption.

Issue #44 item 3. The shutdown handler used to tear down every spawned
backend unconditionally. On ``/admin/api/hub/restart`` that killed the very
processes ``inherit_running_backends()`` exists to re-adopt, so whisper /
qwen came back DOWN. The fix: skip teardown while a restart is in flight,
flagged via ``backend_process.set_restart_pending()``; the respawned hub
then adopts the survivors.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

import pytest

from src import backend_process as bp
from src import server as server_mod


def _run(coro):
    """Run a coroutine on a fresh thread+loop.

    ``asyncio.run()`` (and ``loop.run_until_complete()`` on the main
    thread) raise ``RuntimeError`` when an outer loop is already running —
    which happens in the full suite after other tests have started one,
    making these tests flaky in isolation-vs-suite ordering. Running on a
    worker thread guarantees a clean asyncio context. Mirrors the helper
    in ``tests/test_services_router.py``.
    """
    import threading

    bucket: dict = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            bucket["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised in caller
            bucket["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if "error" in bucket:
        raise bucket["error"]
    return bucket.get("value")


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

    _run(server_mod._stop_backend_children())

    assert sorted(stopped) == ["qwen", "whisper"]


def test_shutdown_skips_teardown_during_restart(monkeypatch):
    stopped = _patch_backends(monkeypatch)

    bp.set_restart_pending(True)
    _run(server_mod._stop_backend_children())

    # Survivors are left alive for the respawned hub to adopt.
    assert stopped == []
