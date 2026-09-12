"""An unreadable ``webapp_config.json`` is its own state, never "fields unset"
(#585).

``load_webapp_config()`` used to absorb a parse/read failure and hand back
``WebappConfig()`` — the same value a fresh clone with no file gets. Every
caller then acted on defaults it believed were configured: ``auth_token=""``
reads as "enforcement off", so the /admin gate opened to non-loopback
callers, and the tray's first-boot ``ensure_auth_token()`` overwrote the
broken file with a fresh token, silently discarding the password, allowlist
and CORS origins it held.

The loader now raises :class:`~src.webapp_config.WebappConfigError`, and each
caller below takes a deliberate branch — pinned here against a synthetic
malformed file, never the machine's real config.
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app_web import server as admin_server
from app_web.routers import machines as machines_router
from src import server as server_mod
from src import webapp_config as webapp_config_mod
from src.webapp_config import WebappConfig
from tray import tray as tray_mod

_PROXY_HEADERS = {"X-Forwarded-For": "203.0.113.5"}  # forces is_loopback=False
_MALFORMED = '{"auth_token": "synthetic-token", '  # truncated mid-object


@pytest.fixture
def broken_config(monkeypatch, tmp_path):
    """Point the default config path at a synthetic malformed file."""
    path = tmp_path / "webapp_config.json"
    path.write_text(_MALFORMED, encoding="utf-8")
    monkeypatch.setattr(webapp_config_mod, "DEFAULT_CONFIG_PATH", path)
    return path


# ---- the loader ----

@pytest.mark.parametrize(
    "content",
    [
        _MALFORMED,                          # not JSON
        "[]",                                # JSON, not an object
        '{"extra_allowlist": 5}',            # an object with an unusable field
    ],
)
def test_loader_reports_unreadable_config_as_its_own_state(tmp_path, content):
    path = tmp_path / "webapp_config.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(webapp_config_mod.WebappConfigError):
        webapp_config_mod.load_webapp_config(path)


def test_loader_still_defaults_when_the_file_is_absent(tmp_path):
    """The contrast case: no file is a legitimate "nothing configured yet"."""
    assert webapp_config_mod.load_webapp_config(tmp_path / "missing.json") == WebappConfig()


# ---- writers: refuse, never overwrite ----

def test_ensure_auth_token_refuses_to_overwrite_an_unreadable_config(broken_config):
    with pytest.raises(webapp_config_mod.WebappConfigError):
        webapp_config_mod.ensure_auth_token()
    assert broken_config.read_text(encoding="utf-8") == _MALFORMED


def test_update_webapp_config_refuses_to_overwrite_an_unreadable_config(broken_config):
    with pytest.raises(webapp_config_mod.WebappConfigError):
        webapp_config_mod.update_webapp_config(auth_password="x")
    assert broken_config.read_text(encoding="utf-8") == _MALFORMED


# ---- parent hub ----

def test_parent_malformed_config_refuses_non_loopback(broken_config, caplog):
    """The per-request token read takes the restrictive branch (#558 covered
    only a non-object; malformed JSON was still absorbed into ``""``)."""
    client = TestClient(server_mod.app)
    with caplog.at_level(logging.WARNING, logger=server_mod.logger.name):
        r = client.get("/v1/models", headers=_PROXY_HEADERS)
    assert r.status_code == 401
    assert "could not load webapp_config for the bearer gate" in caplog.text


def test_parent_startup_load_continues_without_extras_and_logs(broken_config, caplog):
    """Startup can carry on — no extra allowlist entries and loopback-only CORS
    are both the restrictive side — but it says so."""
    with caplog.at_level(logging.WARNING, logger=server_mod.logger.name):
        assert server_mod._load_startup_webapp_config() is None
    assert "extra_allowlist and cors_allow_origins are off" in caplog.text


# ---- /admin sub-app ----

@pytest.fixture
def admin_client(broken_config, caplog):
    with caplog.at_level(logging.WARNING, logger=admin_server._log.name):
        app = admin_server.create_app()
    assert app.state.webapp_config is None
    assert "could not load webapp_config" in caplog.text
    return TestClient(app)


def test_admin_malformed_config_refuses_non_loopback(admin_client):
    r = admin_client.get("/api/version", headers=_PROXY_HEADERS)  # exempt: still up
    assert r.status_code == 200
    r = admin_client.get("/api/webauthn/status", headers=_PROXY_HEADERS)
    assert r.status_code == 401


def test_admin_malformed_config_login_reports_unreadable(admin_client):
    r = admin_client.post("/api/login", json={"password": "anything"})
    assert r.status_code == 503
    assert r.json()["detail"] == "webapp config could not be loaded"


def test_admin_malformed_config_webauthn_reports_unreadable(admin_client):
    """Loopback gets past the gate, and the router reports unknown rather
    than ``configured: false``."""
    r = admin_client.get("/api/webauthn/status")
    assert r.status_code == 503
    assert r.json()["detail"] == "webapp config could not be loaded"


def test_admin_malformed_config_refuses_non_loopback_websocket(admin_client, monkeypatch):
    async def _refuse(*_a, **_kw):
        return {"ok": False, "error": "stubbed in tests"}

    monkeypatch.setattr(machines_router.ssh_terminal, "create_ssh_session", _refuse)
    with admin_client as client:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect(
                "/api/machines/tower/terminal", headers=_PROXY_HEADERS
            ):
                pass
    assert excinfo.value.code == 1008


# ---- tray ----

class _StubHub:
    def base_url(self) -> str:
        return "http://127.0.0.1:8000"


@pytest.fixture
def tray_app(broken_config, monkeypatch, caplog):
    monkeypatch.setattr(tray_mod, "HubProcess", _StubHub)
    monkeypatch.setattr(tray_mod, "local_models", lambda: [])
    with caplog.at_level(logging.WARNING, logger=tray_mod.logger.name):
        app = tray_mod.TrayApp(SimpleNamespace())
    assert "could not load webapp_config" in caplog.text
    return app


def test_tray_boot_leaves_an_unreadable_config_untouched(tray_app, broken_config):
    assert tray_app.webapp_cfg is None
    assert broken_config.read_text(encoding="utf-8") == _MALFORMED


def test_tray_tunnel_copy_refuses_without_a_known_token(tray_app, monkeypatch):
    """Copying a token-less tunnel URL would hand out a link that 401s."""
    notes, copied = [], []
    monkeypatch.setattr(tray_mod, "_read_tunnel_hostname", lambda _p: "llm.example.com")
    monkeypatch.setattr(tray_mod, "_set_clipboard", copied.append)
    monkeypatch.setattr(tray_app, "_notify", lambda title, msg: notes.append(msg))
    tray_app._handle_event(tray_mod.EVT_COPY_TUNNEL)
    assert copied == []
    assert notes and "could not be loaded" in notes[0]


def test_tray_open_admin_skips_the_lan_token_url(tray_app, monkeypatch):
    opened, copied = [], []
    monkeypatch.setattr(tray_mod.webbrowser, "open", opened.append)
    monkeypatch.setattr(tray_mod, "lan_ip", lambda: "192.168.1.10")
    monkeypatch.setattr(tray_mod, "_set_clipboard", copied.append)
    tray_app._open_admin()
    assert opened == ["http://127.0.0.1:8000/admin/"]
    assert copied == []
