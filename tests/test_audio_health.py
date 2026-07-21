"""Regression tests for issue #147: whisper proxy 502 when :8090 is down.

Two behaviours are pinned:

1. When the whisper backend port refuses the connection (whisper-server not
   running), the proxy returns a *distinct* ``503`` naming the port and saying
   the backend isn't running — not the opaque
   ``502 "whisper upstream error: All connection attempts failed"`` that gave
   downstream consumers no way to tell "down" from "in flight past timeout".
2. ``GET /v1/audio/health`` lets a consumer preflight backend liveness without
   sending a doomed transcription: 200 + ``status=ok`` when reachable, 503 +
   ``status=degraded`` when at least one audio backend is down.
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src import server as server_mod  # noqa: E402


def _minimal_wav_bytes() -> bytes:
    import io as _io
    import wave

    buf = _io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 100)
    return buf.getvalue()


def _patch_async_client_raising(monkeypatch, exc: Exception) -> None:
    """Make the AsyncClient used inside _proxy_audio raise on POST."""

    class _RaisingAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            raise exc

    import httpx as _httpx_mod
    monkeypatch.setattr(_httpx_mod, "AsyncClient", _RaisingAsyncClient)


# ---------------------------------------------------------------------------
# 1. Distinct error on connection failure
# ---------------------------------------------------------------------------

def test_connect_error_returns_distinct_503(monkeypatch):
    """A refused connection to whisper-server → 503 naming the port, not 502."""
    _patch_async_client_raising(
        monkeypatch, httpx.ConnectError("All connection attempts failed")
    )

    client = TestClient(server_mod.app)
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("audio.wav", _minimal_wav_bytes(), "audio/wav")},
    )

    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert "whisper-server not running" in detail
    assert "All connection attempts failed" not in detail


def test_transient_httperror_stays_502(monkeypatch):
    """A non-connect upstream error (e.g. read failure) stays a 502."""
    _patch_async_client_raising(monkeypatch, httpx.ReadError("stream broke"))

    client = TestClient(server_mod.app)
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("audio.wav", _minimal_wav_bytes(), "audio/wav")},
    )

    assert resp.status_code == 502, resp.text
    assert "whisper-server upstream error" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 2. Preflight health endpoint
# ---------------------------------------------------------------------------

def test_audio_health_ok_when_reachable(monkeypatch):
    """All audio backends reachable → 200 + status=ok."""
    monkeypatch.setattr(
        "src.backend_process.is_reachable", lambda m, timeout=1.0: True
    )

    client = TestClient(server_mod.app)
    resp = client.get("/v1/audio/health")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["backends"], "expected at least one enabled audio backend"
    assert all(b["reachable"] for b in body["backends"])
    # Every reported backend names a concrete port to preflight against.
    assert all(isinstance(b["port"], int) for b in body["backends"])


def test_audio_health_degraded_when_down(monkeypatch):
    """A down audio backend → 503 + status=degraded (preflight catches it)."""
    monkeypatch.setattr(
        "src.backend_process.is_reachable", lambda m, timeout=1.0: False
    )

    client = TestClient(server_mod.app)
    resp = client.get("/v1/audio/health")

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["status"] == "degraded"
    assert any(not b["reachable"] for b in body["backends"])
