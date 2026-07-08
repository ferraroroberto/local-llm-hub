"""Unit tests for POST /v1/metrics (src/server_otel_receiver.py, issue #68).

`ingest_export_request` itself is covered in tests/test_claude_code_otel.py;
these tests focus on the route contract: always 200, correct content-type,
never surfaces an ingestion failure to the caller.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src import server as server_mod
from src import server_otel_receiver as receiver_mod


def test_receive_otlp_metrics_returns_200_and_calls_ingest(monkeypatch):
    seen = {}

    def fake_ingest(raw: bytes) -> int:
        seen["raw"] = raw
        return 2

    monkeypatch.setattr(receiver_mod, "ingest_export_request", fake_ingest)

    client = TestClient(server_mod.app)
    r = client.post("/v1/metrics", content=b"some-protobuf-bytes", headers={"Content-Type": "application/x-protobuf"})

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-protobuf"
    assert r.content == b""
    assert seen["raw"] == b"some-protobuf-bytes"


def test_receive_otlp_metrics_swallows_ingest_failure(monkeypatch):
    def fake_ingest(raw: bytes) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(receiver_mod, "ingest_export_request", fake_ingest)

    client = TestClient(server_mod.app)
    r = client.post("/v1/metrics", content=b"whatever")

    assert r.status_code == 200
    assert r.content == b""


def test_receive_otlp_metrics_unauthenticated():
    """No bearer token / auth header required — same posture as /v1/messages."""
    client = TestClient(server_mod.app)
    r = client.post("/v1/metrics", content=b"")
    assert r.status_code == 200
