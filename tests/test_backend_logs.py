"""Managed-backend stdout/stderr is captured to a child-owned log file.

Issue #107. Backends used to be spawned with ``stdout=PIPE`` tied to a
hub-owned reader thread + an unread in-memory ring. That made the log
write-only (no reader) and, worse, left an *inherited* backend writing into
the old hub's closed pipe — the ``[Errno 22]`` class that made #104 possible.

The fix redirects each backend's stdout/stderr to
``data/logs/backend-<id>.log``, owned by the child, so the log is:
  * readable on disk and via ``log_lines`` / the admin log endpoint, and
  * restart-safe — the child keeps the fd when the hub exits.

These tests drive the real ``start()`` spawn path with a trivial command so
they run on CI (no model weights, no GPU). ``LOG_DIR`` is redirected to a
tmp dir so we never touch the repo's ``data/logs``.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src import backend_process as bp  # noqa: E402


# --------------------------------------------------------------------------
# spawn → file capture, rolling, restart-safety
# --------------------------------------------------------------------------

def _echo_cmd(token: str) -> list[str]:
    """A throwaway command that prints *token* and exits — stands in for a
    real backend binary so the spawn path is exercised without weights."""
    return [sys.executable, "-c", f"import sys; sys.stdout.write({token!r} + '\\n')"]


def _patch_spawn(monkeypatch, tmp_path, token: str) -> None:
    """Point LOG_DIR at a tmp dir and stub out registry/launch dependencies
    so ``start()`` spawns our echo command instead of a model binary."""
    monkeypatch.setattr(bp, "LOG_DIR", tmp_path)
    monkeypatch.setattr(bp, "resolve_model_by_id", lambda mid: object())
    monkeypatch.setattr(bp, "is_reachable", lambda *a, **k: False)
    monkeypatch.setattr(bp, "vendor_dir_for", lambda m: bp.PROJECT_ROOT)
    monkeypatch.setattr(bp, "build_command", lambda m: _echo_cmd(token))


def _start_and_wait(model_id: str, timeout: float = 15.0) -> None:
    ok, msg = bp.start(model_id)
    assert ok, msg
    proc = bp._state_for(model_id).proc
    assert proc is not None
    proc.wait(timeout=timeout)


def test_start_captures_stdout_to_per_backend_file(monkeypatch, tmp_path):
    _patch_spawn(monkeypatch, tmp_path, "HELLO_LOG")
    _start_and_wait("xqwen")

    log_file = tmp_path / "backend-xqwen.log"
    assert log_file.exists()
    assert "HELLO_LOG" in log_file.read_text(encoding="utf-8")
    assert any("HELLO_LOG" in ln for ln in bp.log_lines("xqwen"))


def test_start_rolls_previous_log_to_backup(monkeypatch, tmp_path):
    _patch_spawn(monkeypatch, tmp_path, "RUN_ONE")
    _start_and_wait("xglm")

    # Second launch must roll the first run's log to .log.1 and start fresh.
    monkeypatch.setattr(bp, "build_command", lambda m: _echo_cmd("RUN_TWO"))
    _start_and_wait("xglm")

    current = tmp_path / "backend-xglm.log"
    backup = tmp_path / "backend-xglm.log.1"
    assert "RUN_TWO" in current.read_text(encoding="utf-8")
    assert "RUN_ONE" not in current.read_text(encoding="utf-8")
    assert backup.exists()
    assert "RUN_ONE" in backup.read_text(encoding="utf-8")


def test_log_lines_survives_state_loss(monkeypatch, tmp_path):
    """A hub restart drops in-process state; ``log_lines`` reads the file, so
    the tail is still available for the inherited backend."""
    _patch_spawn(monkeypatch, tmp_path, "PERSISTED")
    _start_and_wait("xwhisper")

    # Simulate the respawned hub: no _BackendState carried over.
    bp._STATES.pop("xwhisper", None)

    lines = bp.log_lines("xwhisper")
    assert any("PERSISTED" in ln for ln in lines)


def test_log_lines_empty_when_never_started(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "LOG_DIR", tmp_path)
    assert bp.log_lines("never-ran") == []


def test_clear_log_truncates_file(monkeypatch, tmp_path):
    _patch_spawn(monkeypatch, tmp_path, "TO_BE_CLEARED")
    _start_and_wait("xtts")
    assert bp.log_lines("xtts")  # non-empty

    bp.clear_log("xtts")
    assert bp.log_lines("xtts") == []


def test_log_lines_respects_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "LOG_DIR", tmp_path)
    log_file = tmp_path / "backend-many.log"
    log_file.write_text("\n".join(str(i) for i in range(50)) + "\n", encoding="utf-8")

    tail = bp.log_lines("many", limit=5)
    assert tail == ["45", "46", "47", "48", "49"]


# --------------------------------------------------------------------------
# admin endpoint: GET /api/models/{id}/log
# --------------------------------------------------------------------------

def _admin_client() -> TestClient:
    from app_web.server import create_app

    return TestClient(create_app())


def test_log_endpoint_returns_tail(monkeypatch):
    monkeypatch.setattr(bp, "log_lines", lambda mid, limit=400: ["line-a", "line-b"])

    resp = _admin_client().get("/api/models/whisper/log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "whisper"
    assert body["lines"] == ["line-a", "line-b"]
    assert body["path"] == "data/logs/backend-whisper.log"


def test_log_endpoint_unknown_model_404():
    resp = _admin_client().get("/api/models/not-a-real-model/log")
    assert resp.status_code == 404


def test_log_endpoint_subscription_backed_400(monkeypatch):
    class _FakeClaude:
        backend = "claude"

    monkeypatch.setattr(bp, "resolve_model_by_id", lambda mid: _FakeClaude())

    resp = _admin_client().get("/api/models/claude_haiku/log")
    assert resp.status_code == 400
