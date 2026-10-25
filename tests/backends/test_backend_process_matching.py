"""backend_process matching/readiness regressions (#618).

Neither test spawns a process — they pin two narrow bugs found by the
codebase audit:

1. ``_looks_like_backend_binary`` sent every ``backend: whisper`` row into
   the whisper-server.exe check, so a ``backend: whisper`` row on a
   *different* engine (Parakeet's ``engine: parakeet-server``, a
   ``python -m src.parakeet_server`` process) never matched and was never
   inherited across a hub restart.
2. ``is_reachable`` treated a ``tts-server`` shim's 200 ``/health`` as ready
   the instant the process started, before the engine finished loading in
   its background thread.
"""

from __future__ import annotations

from src import backend_process as bp
from src.model_registry import Model


def _model(**overrides) -> Model:
    base = dict(id="m", display_name="m", backend="whisper", port=9999)
    base.update(overrides)
    return Model(**base)


def test_looks_like_backend_binary_ignores_engine_for_whisper_backend():
    """A `backend: whisper` row on a non-whisper-server engine (Parakeet)
    must fall through to the generic python-shim match, not the
    whisper-server.exe-only check."""
    parakeet = _model(engine="parakeet-server")
    cmdline = [r"C:\repo\.venv\Scripts\python.exe", "-m", "src.parakeet_server"]

    assert bp._looks_like_backend_binary(
        r"C:\repo\.venv\Scripts\python.exe", parakeet, cmdline
    ) is True


def test_looks_like_backend_binary_still_matches_real_whisper_server():
    whisper = _model(engine="whisper-server")
    assert bp._looks_like_backend_binary("whisper-server.exe", whisper) is True


def test_is_reachable_tts_server_requires_ready_flag_in_body(monkeypatch):
    """A tts-server shim answers /health 200 as soon as the process starts,
    well before `state.ready` flips true in its background load thread —
    is_reachable must not report the row up until the body says ready."""
    tts_row = _model(backend="tts", engine="tts-server")

    class _Resp:
        def __init__(self, ready: bool):
            self.status_code = 200
            self._ready = ready

        def json(self):
            return {"ok": True, "ready": self._ready}

    class _Client:
        def __init__(self, ready: bool):
            self._ready = ready

        def get(self, url, timeout=None):
            assert url.endswith("/health")
            return _Resp(self._ready)

    monkeypatch.setattr(bp, "get_sync_client", lambda: _Client(False))
    assert bp.is_reachable(tts_row) is False

    monkeypatch.setattr(bp, "get_sync_client", lambda: _Client(True))
    assert bp.is_reachable(tts_row) is True
