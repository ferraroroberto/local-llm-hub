"""Unit tests for app_web/routers/code_usage.py (issue #280).

Mirrors ``test_telemetry_router.py``: through the parent hub's FastAPI app via
TestClient.  With ``AGENTSVIEW_BASE_URL=""`` (conftest) the summary must carry
a disabled/unreachable ``agentsview`` block without raising — the Code-tab
mirror of the Telemetry tab's ``langfuse_reachable`` contract.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src import agentsview_usage as av
from src import server as server_mod


def _client() -> TestClient:
    return TestClient(server_mod.app)


def test_summary_carries_agentsview_block():
    av._reset_for_tests()  # defensive: the snapshot is a module singleton
    r = _client().get("/admin/api/code/usage/summary?period=today&vendor=all")
    assert r.status_code == 200, r.text
    body = r.json()
    block = body.get("agentsview")
    assert block is not None, body.keys()
    for key in ("enabled", "reachable", "vendors", "error", "version"):
        assert key in block, block
    # conftest disables the integration: no probe, not reachable.
    assert block["enabled"] is False
    assert block["reachable"] is False
    assert block["vendors"] == []


def test_unknown_vendor_coerces_to_all():
    r = _client().get("/admin/api/code/usage/summary?vendor=bogus")
    assert r.status_code == 200, r.text
    assert r.json()["vendor"] == "all"


def test_discovered_vendor_accepted(monkeypatch):
    av._reset_for_tests()
    snap = av._Snapshot(vendors=["gemini"], reachable=True)
    monkeypatch.setattr(av, "_snapshot", snap)

    r = _client().get("/admin/api/code/usage/summary?vendor=gemini")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["vendor"] == "gemini"
    assert body["agentsview"]["vendors"] == ["gemini"]

    av._reset_for_tests()
