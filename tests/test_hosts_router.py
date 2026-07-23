"""Unit tests for app_web/routers/hosts.py (issue #181, host-generic since #368).

The router itself takes ``host_id`` as a path param and forwards straight to
``src.remote_bootstrap`` — no per-host branching. These tests pin that with
stubs for two different peer ids (mac-mini-m4, gaming) so a future regression
that special-cases one host over the other fails loudly (#372).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src import remote_bootstrap
from src import server as server_mod


def _client() -> TestClient:
    return TestClient(server_mod.app)


def test_bootstrap_dispatches_with_the_given_host_id(monkeypatch):
    calls: list = []

    async def fake_bootstrap_host(host_id):
        calls.append(host_id)
        return {"ok": True, "detail": f"bootstrapped {host_id}"}

    monkeypatch.setattr(remote_bootstrap, "bootstrap_host", fake_bootstrap_host)

    for host_id in ("mac-mini-m4", "gaming"):
        r = _client().post(f"/admin/api/hosts/{host_id}/bootstrap")
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

    assert calls == ["mac-mini-m4", "gaming"]


def test_sync_dispatches_with_the_given_host_id(monkeypatch):
    calls: list = []

    async def fake_sync_host(host_id):
        calls.append(host_id)
        return {"ok": True, "detail": f"synced {host_id}"}

    monkeypatch.setattr(remote_bootstrap, "sync_host", fake_sync_host)

    for host_id in ("mac-mini-m4", "gaming"):
        r = _client().post(f"/admin/api/hosts/{host_id}/sync")
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

    assert calls == ["mac-mini-m4", "gaming"]


def test_bootstrap_failure_returns_502(monkeypatch):
    async def fake_bootstrap_host(host_id):
        return {"ok": False, "detail": "unreachable"}

    monkeypatch.setattr(remote_bootstrap, "bootstrap_host", fake_bootstrap_host)

    r = _client().post("/admin/api/hosts/gaming/bootstrap")
    assert r.status_code == 502
    assert r.json()["detail"] == "unreachable"


def test_sync_failure_returns_502(monkeypatch):
    async def fake_sync_host(host_id):
        return {"ok": False, "detail": "git pull failed"}

    monkeypatch.setattr(remote_bootstrap, "sync_host", fake_sync_host)

    r = _client().post("/admin/api/hosts/mac-mini-m4/sync")
    assert r.status_code == 502
    assert r.json()["detail"] == "git pull failed"
