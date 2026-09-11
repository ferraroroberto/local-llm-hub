"""Unit tests for app_web/middleware.py's shared ``_authenticate`` helper
(issue #195): BearerTokenMiddleware (the /admin sub-app) and
ParentBearerTokenMiddleware (the parent hub app) both delegate to it now
instead of each carrying its own near-identical dispatch body. These tests
exercise the actual token-configured + non-loopback path, which had no
direct coverage before (indirect coverage only, via other endpoint tests
that all ran token-less/loopback).
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

from fastapi.testclient import TestClient

from app_web import server as admin_server
from src import webapp_config as webapp_config_mod
from src.webapp_config import WebappConfig
from src import server as server_mod

_PROXY_HEADERS = {"X-Forwarded-For": "203.0.113.5"}  # forces is_loopback=False


def _admin_client(token: str) -> TestClient:
    app = admin_server.create_app()
    app.state.webapp_config = WebappConfig(auth_token=token)
    return TestClient(app)


def test_admin_blocks_non_loopback_without_token():
    client = _admin_client("secret123")
    r = client.get("/api/webauthn/status", headers=_PROXY_HEADERS)
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers


def test_admin_allows_non_loopback_with_correct_token():
    client = _admin_client("secret123")
    r = client.get(
        "/api/webauthn/status",
        headers={**_PROXY_HEADERS, "Authorization": "Bearer secret123"},
    )
    assert r.status_code == 200


def test_admin_blocks_non_loopback_with_wrong_token():
    client = _admin_client("secret123")
    r = client.get(
        "/api/webauthn/status",
        headers={**_PROXY_HEADERS, "Authorization": "Bearer nope"},
    )
    assert r.status_code == 401


def test_admin_exempt_path_bypasses_even_without_token():
    client = _admin_client("secret123")
    r = client.get("/api/healthz", headers=_PROXY_HEADERS)
    assert r.status_code == 200


def test_admin_loopback_bypasses_without_token():
    client = _admin_client("secret123")
    r = client.get("/api/webauthn/status")  # no proxy headers -> loopback
    assert r.status_code == 200


def _patch_parent_token(monkeypatch, token: str) -> None:
    # _hub_get_token() (src/server.py) re-reads config/webapp_config.json on
    # every check via load_webapp_config() — it does not read
    # app.state.webapp_config — so the token must be patched at that source.
    monkeypatch.setattr(
        webapp_config_mod, "load_webapp_config", lambda *a, **k: WebappConfig(auth_token=token)
    )


def test_parent_blocks_non_loopback_without_token(monkeypatch):
    _patch_parent_token(monkeypatch, "parentsecret")
    client = TestClient(server_mod.app)
    r = client.get("/v1/models", headers=_PROXY_HEADERS)
    assert r.status_code == 401


def test_parent_allows_non_loopback_with_correct_token(monkeypatch):
    _patch_parent_token(monkeypatch, "parentsecret")
    client = TestClient(server_mod.app)
    r = client.get(
        "/v1/models",
        headers={**_PROXY_HEADERS, "Authorization": "Bearer parentsecret"},
    )
    assert r.status_code == 200


def test_parent_allows_non_loopback_with_correct_api_key(monkeypatch):
    """``x-api-key`` is an equal alternative to the bearer token (#461) — an
    Anthropic SDK configured the ordinary way authenticates without any
    ``default_headers`` special-casing."""
    _patch_parent_token(monkeypatch, "parentsecret")
    client = TestClient(server_mod.app)
    r = client.get(
        "/v1/models",
        headers={**_PROXY_HEADERS, "x-api-key": "parentsecret"},
    )
    assert r.status_code == 200


def test_parent_blocks_non_loopback_with_wrong_api_key(monkeypatch):
    _patch_parent_token(monkeypatch, "parentsecret")
    client = TestClient(server_mod.app)
    r = client.get("/v1/models", headers={**_PROXY_HEADERS, "x-api-key": "nope"})
    assert r.status_code == 401


def test_parent_loopback_still_bypasses_with_junk_api_key(monkeypatch):
    """Loopback bypass is unchanged — an SDK's default ``api_key="local-dummy"``
    must not start failing locally now that the header is read."""
    _patch_parent_token(monkeypatch, "parentsecret")
    client = TestClient(server_mod.app)
    r = client.get("/v1/models", headers={"x-api-key": "local-dummy"})
    assert r.status_code == 200


def test_parent_exempt_path_bypasses_even_without_token(monkeypatch):
    _patch_parent_token(monkeypatch, "parentsecret")
    client = TestClient(server_mod.app)
    r = client.get("/health", headers=_PROXY_HEADERS)
    assert r.status_code == 200


def _break_parent_config(monkeypatch, tmp_path) -> None:
    # Valid JSON that isn't an object — load_webapp_config() only absorbs
    # OSError/JSONDecodeError, so this raises out of it (as would a
    # non-list ``extra_allowlist``) and reaches _hub_get_token's except.
    broken = tmp_path / "webapp_config.json"
    broken.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(webapp_config_mod, "DEFAULT_CONFIG_PATH", broken)


def test_parent_unloadable_config_logs_and_refuses_non_loopback(monkeypatch, tmp_path, caplog):
    """A config that fails to load leaves the token *unknown* (#558). That
    used to come back as "" — indistinguishable from "no token configured" —
    so the gate silently opened to every non-loopback caller, with no log."""
    _break_parent_config(monkeypatch, tmp_path)
    client = TestClient(server_mod.app)
    with caplog.at_level("WARNING", logger=server_mod.logger.name):
        r = client.get("/v1/models", headers=_PROXY_HEADERS)
    assert r.status_code == 401
    assert "could not load webapp_config for the bearer gate" in caplog.text


def test_parent_unloadable_config_still_admits_loopback(monkeypatch, tmp_path):
    _break_parent_config(monkeypatch, tmp_path)
    client = TestClient(server_mod.app)
    r = client.get("/v1/models")  # no proxy headers -> loopback
    assert r.status_code == 200


def test_parent_no_token_configured_still_admits_non_loopback(monkeypatch):
    """The contrast case: an empty ``auth_token`` is an explicit "enforcement
    off" (WebappConfig docs) and must keep admitting non-loopback callers."""
    _patch_parent_token(monkeypatch, "")
    client = TestClient(server_mod.app)
    r = client.get("/v1/models", headers=_PROXY_HEADERS)
    assert r.status_code == 200
