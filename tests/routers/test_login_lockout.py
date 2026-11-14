"""The password route locks out repeated wrong passwords and alerts the owner.

Every value is made up. The clock is fake and the notifier is a recorder, so
nothing sleeps and no Telegram message is sent.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest
from fastapi.testclient import TestClient

from app_web import server as admin_server
from app_web.routers import auth
from src import login_alert
from src.webapp_config import WebappConfig

PASSWORD = "correct-horse"
TOKEN = "tok-abc-123"
# A forwarded address makes the request a tunnelled one, not the PC itself.
TUNNELLED = {"X-Forwarded-For": "203.0.113.5"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SyncThread:
    """Runs the alert delivery inline so the test needs no wait."""

    def __init__(self, target, **_kwargs: Any) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


@pytest.fixture
def transport(tmp_path: Path) -> Path:
    """A stand-in for the fleet transport script, so the channel counts as configured."""
    script = tmp_path / "notify_send.py"
    script.write_text("", encoding="utf-8")
    return script


@pytest.fixture
def harness(monkeypatch, transport):
    monkeypatch.setattr(login_alert.threading, "Thread", SyncThread)
    sent: List[Tuple[str, Dict[str, Any]]] = []

    def fake_send(text: str, cfg: Dict[str, Any]):
        sent.append((text, cfg))
        return True, None

    clock = FakeClock()
    app = admin_server.create_app()
    app.state.webapp_config = WebappConfig(auth_password=PASSWORD, auth_token=TOKEN)
    app.state.login_lockout = auth.build_lockout(
        send=fake_send, transport_script=transport, clock=clock
    )
    return TestClient(app), clock, sent


def _wrong(client: TestClient, headers=None):
    return client.post("/api/login", json={"password": "nope"}, headers=headers)


def test_six_wrong_passwords_are_401_then_429_with_retry_after(harness):
    client, clock, _ = harness
    assert [_wrong(client, TUNNELLED).status_code for _ in range(6)] == [401] * 6
    locked = _wrong(client, TUNNELLED)
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) >= 1

    # A locked caller is refused even with the right password, until the wait ends.
    refused = client.post("/api/login", json={"password": PASSWORD}, headers=TUNNELLED)
    assert refused.status_code == 429

    clock.now += float(locked.headers["Retry-After"])
    ok = client.post("/api/login", json={"password": PASSWORD}, headers=TUNNELLED)
    assert ok.status_code == 200
    assert ok.json() == {"token": TOKEN}


def test_one_alert_per_streak_without_password_or_token(harness):
    client, _, sent = harness
    for _ in range(3):
        _wrong(client, TUNNELLED)
    assert len(sent) == 1
    text, cfg = sent[0]
    assert cfg["category"] == "attention"
    assert auth.APP_NAME in text and "203.0.113.5" in text and "3 wrong" in text
    assert "nope" not in text and PASSWORD not in text and TOKEN not in text

    for _ in range(3):
        _wrong(client, TUNNELLED)
    assert len(sent) == 1


def test_the_pc_itself_is_never_locked_out_or_alerted(harness):
    client, _, sent = harness
    assert {_wrong(client).status_code for _ in range(12)} == {401}
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    assert sent == []


def test_an_off_box_page_borrowing_the_pc_is_not_exempt(harness):
    client, _, _ = harness
    foreign = {"Origin": "https://evil.example"}
    codes = [_wrong(client, foreign).status_code for _ in range(7)]
    assert codes[-1] == 429


def test_tunnelled_clients_are_keyed_by_the_tunnel_header(harness):
    client, _, _ = harness
    first = {"cf-connecting-ip": "203.0.113.5"}
    second = {"cf-connecting-ip": "203.0.113.6"}
    for _ in range(7):
        _wrong(client, first)
    assert _wrong(client, first).status_code == 429
    assert _wrong(client, second).status_code == 401


def test_without_the_transport_the_alert_is_unconfigured(harness, tmp_path, caplog):
    client, clock, sent = harness
    client.app.state.login_lockout = auth.build_lockout(
        send=lambda text, cfg: (sent.append((text, cfg)) or (True, None)),
        transport_script=tmp_path / "absent.py",
        clock=clock,
    )
    with caplog.at_level(logging.WARNING, logger="src.login_alert"):
        for _ in range(3):
            _wrong(client, TUNNELLED)
    assert sent == []
    assert "unconfigured" in caplog.text and "3 wrong passwords" in caplog.text


def test_an_undelivered_alert_is_logged_as_failed(harness, transport, caplog):
    client, clock, _ = harness
    client.app.state.login_lockout = auth.build_lockout(
        send=lambda text, cfg: (False, "notify script exited 1"),
        transport_script=transport,
        clock=clock,
    )
    with caplog.at_level(logging.WARNING, logger="src.login_alert"):
        for _ in range(3):
            _wrong(client, TUNNELLED)
    assert "login alert failed" in caplog.text


def test_a_forwarding_header_from_a_non_loopback_peer_is_ignored(harness, transport):
    _, clock, sent = harness
    app = admin_server.create_app()
    app.state.webapp_config = WebappConfig(auth_password=PASSWORD, auth_token=TOKEN)
    app.state.login_lockout = auth.build_lockout(
        send=lambda text, cfg: (sent.append((text, cfg)) or (True, None)),
        transport_script=transport,
        clock=clock,
    )
    remote = TestClient(app, client=("198.51.100.9", 50000))
    for n in range(7):
        # A rotating forged header must not buy a fresh allowance.
        remote.post(
            "/api/login", json={"password": "nope"},
            headers={"cf-connecting-ip": f"203.0.113.{n + 10}"},
        )
    assert remote.post("/api/login", json={"password": "nope"}).status_code == 429
