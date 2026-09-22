"""Tests for the TypeSafe ``POST /v1/systemone`` passthrough (#611).

The vendor is stubbed with ``httpx.MockTransport`` swapped in for the shared
async client — no test ever reaches ``api.typesafe.ai``. Covers the unchanged
passthrough, the key handling, and the distinguishable failure states, each
checked both on the wire and in the observability ring.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from src import server as server_mod
from src import server_systemone as systemone_mod
from src.hub_observability import OBS

_KEY = "ts-test-key-do-not-leak-7f3a"

_REQUEST = {
    "model": "jev-latest",
    "state": "Newsletter issue about local LLM hubs.",
    "questions": {
        "relevant": {"type": "noul", "instructions": "Is this relevant?"},
        "topic": {
            "type": "choice",
            "instructions": "Pick the topic.",
            "criteria": {"ai": "AI", "finance": "Finance"},
        },
        "quality": {
            "type": "score",
            "instructions": "Rate quality.",
            "criteria": ["poor", "ok", "great"],
        },
    },
}

_ANSWER = {
    "model": "jev-1.13.0",
    "answers": {
        "relevant": {"type": "noul", "noul": 0.91},
        "topic": {
            "type": "choice",
            "choice": "ai",
            "probabilities": {"ai": 0.97, "finance": 0.03},
            "confidence": 0.94,
        },
        "quality": {
            "type": "score",
            "score": 2,
            "legend": {"0": "poor", "1": "ok", "2": "great"},
            "probabilities": {"0": 0.05, "1": 0.25, "2": 0.70},
            "confidence": 0.62,
        },
    },
    "usage": {"input_tokens": 412, "output_tokens": 9},
}


@pytest.fixture
def upstream(monkeypatch):
    """Install a stub vendor; returns a dict the test fills with a handler."""
    seen: dict = {}
    state: dict = {"handler": lambda req: httpx.Response(200, json=_ANSWER)}

    def _dispatch(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = req.content
        return state["handler"](req)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_dispatch))
    monkeypatch.setattr(systemone_mod, "get_async_client", lambda: client)
    monkeypatch.setenv("TYPESAFE_API_KEY", _KEY)
    state["seen"] = seen
    return state


def _post(body=None):
    client = TestClient(server_mod.app)
    return client.post("/v1/systemone", content=json.dumps(body or _REQUEST),
                       headers={"content-type": "application/json"})


def _last_ring_entry() -> dict:
    recs = [r for r in OBS.recent_requests(200) if r["path"] == "/v1/systemone"]
    assert recs, "no /v1/systemone entry in the ring"
    return recs[0]


def _assert_no_key_leak(resp: httpx.Response, rec: dict) -> None:
    assert _KEY not in resp.text
    assert _KEY not in json.dumps(rec)


def test_passthrough_returns_vendor_answers_unchanged(upstream):
    def _answer(req):
        # A real wait: Windows' monotonic clock ticks ~15.6 ms, so an instant
        # stub can legitimately measure 0.0 ms and hide a missing latency.
        time.sleep(0.03)
        return httpx.Response(200, json=_ANSWER)
    upstream["handler"] = _answer
    r = _post()
    assert r.status_code == 200
    assert r.json() == _ANSWER  # probabilities + confidence intact
    seen = upstream["seen"]
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["auth"] == f"Bearer {_KEY}"
    assert json.loads(seen["body"]) == _REQUEST

    rec = _last_ring_entry()
    assert rec["status"] == 200
    assert rec["model"] == "jev-latest"
    assert rec["served_model"] == "jev-1.13.0"
    assert rec["backend"] == "typesafe"
    assert (rec["in_tok"], rec["out_tok"]) == (412, 9)
    assert rec["detail"] == "3 questions"
    assert rec["latency_ms"] >= 30
    assert rec["error_detail"] == ""
    _assert_no_key_leak(r, rec)


def test_missing_key_is_explicit_and_never_egresses(upstream, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    r = _post()
    assert r.status_code == 503
    assert r.json()["hub_error"] == "typesafe_not_configured"
    assert "not configured" in r.json()["detail"]
    assert "url" not in upstream["seen"]  # nothing left the hub
    rec = _last_ring_entry()
    assert rec["status"] == 503
    assert rec["error_detail"].startswith("typesafe_not_configured")


@pytest.mark.parametrize("status, label", [
    (401, "auth rejected (401)"),
    (429, "rate limited (429)"),
    (529, "overloaded (529)"),
])
def test_upstream_errors_pass_through_with_distinct_ring_entries(upstream, status, label):
    vendor_body = {"error": {"type": "vendor_error", "message": "nope"}}
    upstream["handler"] = lambda req: httpx.Response(
        status, json=vendor_body, headers={"retry-after": "7"},
    )
    r = _post()
    assert r.status_code == status
    assert r.json() == vendor_body  # vendor body unchanged, no hub_error
    if status in (429, 529):
        assert r.headers["retry-after"] == "7"
    rec = _last_ring_entry()
    assert rec["status"] == status
    assert label in rec["error_detail"]
    _assert_no_key_leak(r, rec)


def test_vendor_error_text_dropped_from_ring_when_hashing(upstream, monkeypatch):
    monkeypatch.setenv("OTEL_HASH_PROMPTS", "true")
    upstream["handler"] = lambda req: httpx.Response(422, text="state echoed: SECRET-INPUT")
    r = _post()
    assert r.status_code == 422
    rec = _last_ring_entry()
    assert rec["error_detail"] == "typesafe request validation failed (422)"


def test_unreachable_upstream(upstream):
    def _boom(req):
        raise httpx.ConnectError("connection refused", request=req)
    upstream["handler"] = _boom
    r = _post()
    assert r.status_code == 502
    assert r.json()["hub_error"] == "typesafe_unreachable"
    rec = _last_ring_entry()
    assert rec["status"] == 502
    assert rec["error_detail"].startswith("typesafe_unreachable")
    _assert_no_key_leak(r, rec)


def test_timeout_is_distinct_from_unreachable(upstream):
    def _slow(req):
        raise httpx.ReadTimeout("timed out", request=req)
    upstream["handler"] = _slow
    r = _post()
    assert r.status_code == 504
    assert r.json()["hub_error"] == "typesafe_timeout"
    assert _last_ring_entry()["error_detail"].startswith("typesafe_timeout")


def test_not_listed_in_models(upstream):
    ids = [m["id"] for m in TestClient(server_mod.app).get("/v1/models").json()["data"]]
    assert not any(i.startswith("jev") for i in ids)
    assert systemone_mod.ADMIN_MODEL_ID not in ids  # the admin tile never leaks into /v1/models


# --------------------------------------------------------------------- #
# Admin surfaces (#611 follow-up): Models-tab tile + Playground Decision card.
# --------------------------------------------------------------------- #

def _admin_models(monkeypatch, **params) -> dict:
    from app_web.routers import models as models_router
    from src import remote_stats

    async def _no_remote(*_a, **_kw):
        return None  # remote-owned rows render offline; nothing leaves the box

    monkeypatch.setattr(models_router, "snapshot_listening_pids", lambda: {})
    monkeypatch.setattr(remote_stats, "remote_models", _no_remote)
    return TestClient(server_mod.app).get("/admin/api/models", params=params).json()


def _jev_rows(body: dict) -> list:
    return [m for m in body["models"] if m["id"] == systemone_mod.ADMIN_MODEL_ID]


def test_models_tab_lists_jev_tile_with_key_state(upstream, monkeypatch):
    rows = _jev_rows(_admin_models(monkeypatch))
    assert len(rows) == 1
    row = rows[0]
    assert row["backend"] == "typesafe"
    assert row["controllable"] is False           # no start/stop — no process
    assert row["reachable"] is True and row["key_configured"] is True
    assert row["aliases"] == list(systemone_mod.MODEL_ALIASES)
    assert _KEY not in json.dumps(row)
    assert upstream["seen"] == {}                 # listing never probes the vendor

    monkeypatch.delenv("TYPESAFE_API_KEY")
    row = _jev_rows(_admin_models(monkeypatch))[0]
    assert row["reachable"] is False and row["key_configured"] is False


def test_models_tab_peer_merge_listing_excludes_jev(upstream, monkeypatch):
    # `local_only` is what a peer hub fetches to merge registry rows; the tile
    # is per-hub key state and must not ride along.
    assert _jev_rows(_admin_models(monkeypatch, local_only="true")) == []


def test_playground_info_reports_key_state_not_key(upstream, monkeypatch):
    client = TestClient(server_mod.app)
    body = client.get("/admin/api/playground/systemone_info").json()
    assert body["models"] == list(systemone_mod.MODEL_ALIASES)
    assert body["key_configured"] is True
    assert _KEY not in json.dumps(body)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert client.get("/admin/api/playground/systemone_info").json()["key_configured"] is False


@pytest.fixture
def loopback(monkeypatch):
    """Route the Playground's loopback call into the in-process hub app, so the
    Decision card is exercised through the real ``/v1/systemone`` route."""
    from app_web.routers import playground as playground_router

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server_mod.app))
    monkeypatch.setattr(playground_router, "get_async_client", lambda: client)


def _evaluate(body=None):
    payload = body or {k: _REQUEST[k] for k in ("model", "state", "questions")}
    return TestClient(server_mod.app).post("/admin/api/playground/systemone", json=payload)


def test_playground_evaluation_goes_through_hub_route(upstream, loopback):
    r = _evaluate()
    assert r.status_code == 200, r.text
    assert r.json() == _ANSWER
    assert json.loads(upstream["seen"]["body"])["questions"] == _REQUEST["questions"]
    rec = _last_ring_entry()                      # lands in the ring like any caller
    assert rec["status"] == 200 and rec["detail"] == "3 questions"


def test_playground_relays_not_configured(upstream, loopback, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    r = _evaluate()
    assert r.status_code == 503
    assert "typesafe_not_configured" in r.json()["detail"]
    assert upstream["seen"] == {}


def test_playground_vendor_401_is_not_an_admin_401(upstream, loopback):
    # A relayed 401 would pop the admin SPA's login overlay for a vendor-key
    # problem — it must travel as a 502 that names the real cause.
    upstream["handler"] = lambda req: httpx.Response(401, json={"detail": "bad key"})
    r = _evaluate()
    assert r.status_code == 502
    assert "rejected the API key (401)" in r.json()["detail"]


def test_playground_rejects_bad_questions_without_egress(upstream, loopback):
    r = _evaluate({"model": "jev-latest", "state": "x", "questions": []})
    assert r.status_code == 400
    r = _evaluate({"model": "jev-latest", "state": "", "questions": {"a": {"type": "noul"}}})
    assert r.status_code == 400
    assert upstream["seen"] == {}
