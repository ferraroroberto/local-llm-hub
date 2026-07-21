"""Regression test for issue #73: _proxy_audio translate bridge.

Before the fix, POST /v1/audio/translations forwarded raw bytes to
/v1/audio/translations on the whisper backend — a path that does not
exist (whisper-server exposes exactly one inference endpoint:
/v1/audio/transcriptions).  The fix parses the multipart form when
default_role == "audio_translate", rewrites task=translate ->
translate=true, and POSTs to /v1/audio/transcriptions.

These tests verify:
1. The translate endpoint POSTs to the backend's /v1/audio/transcriptions
   path (not /v1/audio/translations).
2. The task=translate field is bridged to translate=true in the upstream
   request data.
3. The transcribe endpoint is unaffected — it still forwards raw bytes
   to /v1/audio/transcriptions verbatim.
"""

from __future__ import annotations

import io
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest  # noqa: E402
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src import server as server_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeUpstreamResp:
    status_code = 200
    content = b'{"text": "hello world"}'
    headers = {"content-type": "application/json"}


def _patch_async_client(monkeypatch, calls: list):
    """Replace httpx.AsyncClient used inside _proxy_audio."""

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            calls.append({"url": url, "kwargs": kwargs})
            return _FakeUpstreamResp()

    # _proxy_audio imports httpx as _httpx inside the function body;
    # patch the module-level reference that gets resolved at call time.
    import httpx as _httpx_mod
    monkeypatch.setattr(_httpx_mod, "AsyncClient", _FakeAsyncClient)


def _minimal_wav_bytes() -> bytes:
    """Return a tiny but valid WAV payload (44-byte header, zero frames)."""
    import struct, wave, io as _io
    buf = _io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 100)
    return buf.getvalue()


def _make_translate_request(client: TestClient, extra_fields: dict | None = None):
    """POST /v1/audio/translations with a minimal multipart body."""
    wav = _minimal_wav_bytes()
    files = {"file": ("audio.wav", wav, "audio/wav")}
    data = {"task": "translate"}
    if extra_fields:
        data.update(extra_fields)
    return client.post("/v1/audio/translations", files=files, data=data)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_translate_posts_to_transcriptions_path(monkeypatch):
    """POST /v1/audio/translations must forward to /v1/audio/transcriptions."""
    calls: list = []
    _patch_async_client(monkeypatch, calls)

    client = TestClient(server_mod.app)
    resp = _make_translate_request(client)

    assert resp.status_code == 200
    assert len(calls) == 1
    upstream_url: str = calls[0]["url"]
    assert upstream_url.endswith("/v1/audio/transcriptions"), (
        f"Expected upstream path /v1/audio/transcriptions, got: {upstream_url}"
    )


def test_translate_bridges_task_to_translate_true(monkeypatch):
    """task=translate must be rewritten to translate=true in the upstream form."""
    calls: list = []
    _patch_async_client(monkeypatch, calls)

    client = TestClient(server_mod.app)
    resp = _make_translate_request(client)

    assert resp.status_code == 200
    assert len(calls) == 1
    upstream_data: dict = calls[0]["kwargs"].get("data", {})
    # translate=true must be present
    assert upstream_data.get("translate") == "true", (
        f"Expected translate=true in upstream data, got: {upstream_data}"
    )
    # task= must NOT be forwarded
    assert "task" not in upstream_data, (
        f"task= must not be forwarded to whisper-server; got: {upstream_data}"
    )


def test_translate_forwards_file(monkeypatch):
    """The audio file must arrive in the upstream files dict."""
    calls: list = []
    _patch_async_client(monkeypatch, calls)

    client = TestClient(server_mod.app)
    resp = _make_translate_request(client)

    assert resp.status_code == 200
    assert len(calls) == 1
    upstream_files: dict = calls[0]["kwargs"].get("files", {})
    assert "file" in upstream_files, (
        f"Expected 'file' key in upstream files, got: {list(upstream_files.keys())}"
    )


def test_transcribe_path_unchanged(monkeypatch):
    """POST /v1/audio/transcriptions must still forward to /v1/audio/transcriptions."""
    calls: list = []
    _patch_async_client(monkeypatch, calls)

    wav = _minimal_wav_bytes()
    client = TestClient(server_mod.app)
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("audio.wav", wav, "audio/wav")},
    )

    assert resp.status_code == 200
    assert len(calls) == 1
    upstream_url: str = calls[0]["url"]
    assert upstream_url.endswith("/v1/audio/transcriptions"), (
        f"Transcribe path changed unexpectedly: {upstream_url}"
    )
