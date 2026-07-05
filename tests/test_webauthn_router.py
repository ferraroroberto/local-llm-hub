"""Unit tests for app_web/routers/webauthn.py's enroll/window PC-only gate
(issue #199): a proxied request that arrives with a loopback ``client.host``
(tailscale/cloudflared/nginx forwarding to 127.0.0.1) must not be able to
open the enrollment window — only a genuine, unproxied loopback caller can.

Uses ``app_web.server.create_app()`` directly (not the parent hub app) with
an explicit empty ``auth_token``, so ``BearerTokenMiddleware`` always calls
through and the router's own PC-only check is what's actually exercised —
independent of whatever auth_token happens to be persisted on this machine's
real ``webapp_config.json``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app_web import server as admin_server
from src.webapp_config import WebappConfig


def _client() -> TestClient:
    app = admin_server.create_app()
    app.state.webapp_config = WebappConfig(auth_token="")
    return TestClient(app)


def test_enroll_window_allowed_from_genuine_loopback():
    client = _client()
    r = client.post("/api/webauthn/enroll/window", json={"seconds": 60})
    assert r.status_code == 200, r.text
    assert r.json()["enrollment_open"] is True


def test_enroll_window_blocked_when_forwarded_through_a_proxy():
    """Same loopback client.host (TestClient's is 'testclient', in
    LOOPBACK_HOSTS), but with a reverse-proxy header present — this is
    exactly the tailscale/cloudflared/nginx-forwarding-to-loopback shape
    BearerTokenMiddleware already detects via ``_is_proxied``. Before the
    fix, the endpoint only checked ``client_host`` and let this through.
    """
    client = _client()
    r = client.post(
        "/api/webauthn/enroll/window",
        json={"seconds": 60},
        headers={"X-Forwarded-For": "203.0.113.5"},
    )
    assert r.status_code == 403
    assert "PC" in r.json()["detail"]


def test_enroll_window_blocked_when_cf_ray_header_present():
    """cf-ray is cloudflared's own tunnel header — a second proxy signal
    _is_proxied checks, distinct from X-Forwarded-For."""
    client = _client()
    r = client.post(
        "/api/webauthn/enroll/window",
        json={"seconds": 60},
        headers={"cf-ray": "abc123-EWR"},
    )
    assert r.status_code == 403
