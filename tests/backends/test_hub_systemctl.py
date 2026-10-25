"""Hub stop/restart drive systemctl when supervised by systemd (#368).

On a headless Linux satellite the hub runs under a systemd unit with
``Restart=always``, so a bare self-SIGTERM would be respawned — a deliberate
stop/restart must go through ``systemctl``. These tests assert the endpoints
take that path only when ``_under_systemd()`` is true, without ever scheduling
a real shutdown (``_delayed_systemctl`` is stubbed to a recorder).
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

from fastapi.testclient import TestClient

import app_web.routers.hub as hub_mod
from src import server as server_mod


def _client() -> TestClient:
    return TestClient(server_mod.app)


def test_under_systemd_false_off_linux(monkeypatch):
    monkeypatch.setattr(hub_mod.sys, "platform", "win32")
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    assert hub_mod._under_systemd() is False


def test_under_systemd_true_when_invocation_id_present_on_linux(monkeypatch):
    monkeypatch.setattr(hub_mod.sys, "platform", "linux")
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    assert hub_mod._under_systemd() is True


def test_under_systemd_false_without_invocation_id(monkeypatch):
    monkeypatch.setattr(hub_mod.sys, "platform", "linux")
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert hub_mod._under_systemd() is False


def test_hub_stop_uses_systemctl_under_systemd(monkeypatch):
    calls = []
    monkeypatch.setattr(hub_mod, "_under_systemd", lambda: True)
    monkeypatch.setattr(hub_mod, "_delayed_systemctl", lambda verb: calls.append(verb))
    r = _client().post("/admin/api/hub/stop")
    assert r.status_code == 200, r.text
    assert calls == ["stop"]
    assert "systemctl stop" in r.json()["detail"]


def test_hub_restart_uses_systemctl_under_systemd(monkeypatch):
    from src import backend_process as bp

    calls = []
    monkeypatch.setattr(hub_mod, "_under_systemd", lambda: True)
    monkeypatch.setattr(hub_mod, "_delayed_systemctl", lambda verb: calls.append(verb))
    try:
        r = _client().post("/admin/api/hub/restart")
        assert r.status_code == 200, r.text
        assert calls == ["restart"]
        assert "systemd" in r.json()["detail"]
        # restart_pending is set so the respawned unit adopts live backends.
        assert bp.restart_pending() is True
    finally:
        bp.set_restart_pending(False)
