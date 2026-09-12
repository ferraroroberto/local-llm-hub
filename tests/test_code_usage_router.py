"""Unit tests for app_web/routers/code_usage.py (issue #280).

Mirrors ``test_telemetry_router.py``: through the parent hub's FastAPI app via
TestClient.  With ``AGENTSVIEW_BASE_URL=""`` (conftest) the summary must carry
a disabled/unreachable ``agentsview`` block without raising — the Code-tab
mirror of the Telemetry tab's ``langfuse_reachable`` contract.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app_web.routers import code_usage as router_mod
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


def test_summary_parse_runs_off_the_event_loop(monkeypatch):
    """#559: ``get_summary`` parses session JSONL off disk. Run inline in the
    ``async def`` route it stalls the one loop that also serves every
    ``/v1/*`` request in this process, so it must run in a worker thread."""
    seen = {}

    def fake_get_summary(period, vendor):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return {"period": period, "vendor": vendor}

    monkeypatch.setattr(router_mod, "get_summary", fake_get_summary)
    r = _client().get("/admin/api/code/usage/summary")
    assert r.status_code == 200, r.text
    assert seen == {"on_loop": False}


def test_failed_summary_is_not_a_zero_usage_day(monkeypatch):
    """#580: a summary that could not be built must answer as a failure, never
    as a 200 whose empty totals read like an idle day.

    The old shape returned 200 with zeroed ``totals``/``by_*`` plus an
    ``error`` field the client never read, so a broken load drew the same
    empty chart as a genuinely zero-usage period — the fleet's recurring
    defect of folding "could not determine" into the passing state."""

    def boom(period, vendor):
        raise RuntimeError("transcript store unreadable")

    monkeypatch.setattr(router_mod, "get_summary", boom)
    r = _client().get("/admin/api/code/usage/summary?period=today&vendor=all")

    assert r.status_code == 503, r.text
    body = r.json()
    # The reason reaches the surface, both for the client and for a human
    # reading the response directly.
    assert "transcript store unreadable" in body["error"]
    assert "transcript store unreadable" in body["detail"]
    # Crucially: no zero-shaped payload for a caller to mistake for data.
    for key in ("totals", "daily", "by_model", "by_project", "by_vendor",
                "recent_sessions"):
        assert key not in body, f"{key} present on a failed summary: {body}"


def test_zero_usage_day_still_renders_as_data(monkeypatch):
    """The flip side of #580: an honestly empty period is a *successful*
    summary and must keep its 200 with real (zero) totals — the error state
    is driven by the server reporting failure, never inferred from zeroes."""

    def empty(period, vendor):
        return {
            "period": period, "vendor": vendor,
            "totals": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
            "by_model": [], "by_project": [], "by_vendor": [],
            "recent_sessions": [],
        }

    monkeypatch.setattr(router_mod, "get_summary", empty)
    r = _client().get("/admin/api/code/usage/summary?period=today&vendor=all")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"] == {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    assert "error" not in body


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
