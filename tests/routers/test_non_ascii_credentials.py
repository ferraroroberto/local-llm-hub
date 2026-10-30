"""A non-ASCII credential is a 401, never a 500 (#787).

``hmac.compare_digest`` raises ``TypeError`` when handed a ``str`` holding a
non-ASCII character, so a password / bearer / ``?token=`` like ``é`` used to
escape as an unhandled 500 — and a configured ``auth_password`` with an accent
could never log in. All values here are made up.
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

from fastapi.testclient import TestClient

from app_web import server as admin_server
from src.webapp_config import WebappConfig

_PROXY_HEADERS = {"X-Forwarded-For": "203.0.113.5"}  # forces is_loopback=False


def _client(**cfg) -> TestClient:
    app = admin_server.create_app()
    app.state.webapp_config = WebappConfig(**cfg)
    return TestClient(app)


def test_login_non_ascii_password_is_401_not_500():
    client = _client(auth_password="plain-secret", auth_token="tok-abc")
    r = client.post("/api/login", json={"password": "pässwörd-é"})
    assert r.status_code == 401


def test_login_succeeds_with_a_non_ascii_configured_password():
    client = _client(auth_password="pässwörd-é", auth_token="tok-abc")
    ok = client.post("/api/login", json={"password": "pässwörd-é"})
    assert ok.status_code == 200
    assert ok.json() == {"token": "tok-abc"}
    bad = client.post("/api/login", json={"password": "passwörd-é"})
    assert bad.status_code == 401


def test_non_ascii_bearer_from_remote_caller_is_401_not_500():
    client = _client(auth_token="secret123")
    r = client.get(
        "/api/hub/counters",
        headers={**_PROXY_HEADERS, "Authorization": "Bearer é".encode("utf-8")},
    )
    assert r.status_code == 401


def test_non_ascii_query_token_from_remote_caller_is_401_not_500():
    client = _client(auth_token="secret123")
    r = client.get(
        "/api/hub/counters", params={"token": "é"}, headers=_PROXY_HEADERS
    )
    assert r.status_code == 401


def test_non_ascii_credential_on_foreign_origin_request_is_401_not_500():
    client = _client(auth_token="secret123")
    # POST so the foreign-origin rule applies (GET is a safe method); the path
    # is unrouted, so a refused request is 401 and an admitted one would be 404.
    r = client.post(
        "/api/__request_origin_probe__",
        params={"token": "é"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 401
