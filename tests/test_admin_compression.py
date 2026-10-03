"""Compression on the /admin sub-app (#646, playbook P2).

The entry document and the JS/CSS bundle are the bulk of a cold phone load, so
the sub-app gzips them. Streams and media must stay out: gzip buffers a live
SSE/audio stream and burns CPU on bytes that are already compressed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src import server as server_mod
from tests.test_playground_router import _mock_stream_upstream

_GZIP = {"Accept-Encoding": "gzip"}


def test_entry_document_is_gzipped():
    r = TestClient(server_mod.app).get("/admin/", headers=_GZIP)
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert "<html" in r.text


def test_large_static_script_is_gzipped():
    r = TestClient(server_mod.app).get(
        "/admin/static/_vendored/xterm/xterm.js", headers=_GZIP
    )
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"


def test_small_json_stays_uncompressed():
    # Guards the middleware order: outside the bearer gate gzip sees re-streamed
    # chunks and ignores minimum_size, compressing even a 50-byte body.
    r = TestClient(server_mod.app).get("/admin/api/healthz", headers=_GZIP)
    assert r.status_code == 200
    assert "content-encoding" not in r.headers


def test_streamed_audio_is_not_gzipped(monkeypatch):
    _mock_stream_upstream(monkeypatch, chunk=b"\x01\x00" * 2000)
    r = TestClient(server_mod.app).post(
        "/admin/api/playground/speak",
        data={"model": "chatterbox-tts", "input": "hi", "stream": "true",
              "response_format": "pcm"},
        headers=_GZIP,
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("content-encoding") in (None, "identity")
