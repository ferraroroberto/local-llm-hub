"""A peer hub's health verdict on its Machines card.

The peer is never dialled for real: the address lookup and the HTTP client are
replaced, so the tests pin what the card carries for each outcome of the read.
"""

from __future__ import annotations

import pytest

from src.fleet import machine_console as mc
from src.fleet import peer_health
from src.host_profile import get_host
from tests._worker_loop import run_on_worker_thread as _run

PEER_VERDICT = {
    "verdict": "act", "reason": "leaky.exe at 1.2 M handles", "fix": "Restart leaky.exe",
    "sampled_at": 1.0, "enabled": True, "interval_s": 600, "samples": 9, "alerts": [],
    "findings": [{"level": "act", "kind": "process", "metric": "handles", "value": 1.2,
                  "reason": "leaky.exe at 1.2 M handles", "fix": "Restart leaky.exe"}],
}


class _Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Client:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def get(self, url, headers=None, timeout=None):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture(autouse=True)
def _peer_identity(config_with_example_identity):
    yield


@pytest.fixture(autouse=True)
def _cold_cache():
    peer_health.reset_cache()
    yield
    peer_health.reset_cache()


def _wire(monkeypatch, outcome, address="192.0.2.10"):
    client = _Client(outcome)

    async def _dial(host):
        return address

    monkeypatch.setattr(peer_health, "dial_address_async", _dial)
    monkeypatch.setattr(peer_health, "get_async_client", lambda: client)
    return client


def test_a_readable_verdict_is_summarised_to_the_card_shape(monkeypatch):
    _wire(monkeypatch, _Resp(200, PEER_VERDICT))
    summary = _run(peer_health.peer_health_summary(get_host("mac-mini-m4")))
    assert summary["verdict"] == "act" and summary["fix"] == "Restart leaky.exe"
    assert summary["enabled"] is True
    assert summary["findings"] == [{"level": "act", "kind": "process",
                                    "reason": "leaky.exe at 1.2 M handles", "fix": "Restart leaky.exe"}]
    assert "alerts" not in summary and "samples" not in summary


@pytest.mark.parametrize("outcome", [
    _Resp(404, {}),                       # an older build without the endpoint
    _Resp(200, ValueError("not json")),   # a body that is not JSON
    _Resp(200, {"verdict": "ok"}),        # a body missing the fields the card needs
    TimeoutError("slow"),                 # the request itself failed
])
def test_an_unreadable_verdict_is_not_confirmed_never_fine(monkeypatch, outcome):
    _wire(monkeypatch, outcome)
    summary = _run(peer_health.peer_health_summary(get_host("mac-mini-m4")))
    assert summary["verdict"] == "unknown"
    assert summary["fix"] is None and summary["findings"] == []
    assert summary["enabled"] is True  # still shown: the row says "not confirmed"


def test_no_dial_address_is_not_confirmed(monkeypatch):
    _wire(monkeypatch, _Resp(200, PEER_VERDICT), address=None)
    summary = _run(peer_health.peer_health_summary(get_host("mac-mini-m4")))
    assert summary["verdict"] == "unknown"


def test_a_disabled_peer_watch_stays_disabled(monkeypatch):
    _wire(monkeypatch, _Resp(200, {**PEER_VERDICT, "verdict": "unknown", "enabled": False}))
    summary = _run(peer_health.peer_health_summary(get_host("mac-mini-m4")))
    assert summary["enabled"] is False


def test_a_read_is_cached_and_a_failure_is_retried_sooner(monkeypatch):
    client = _wire(monkeypatch, _Resp(200, PEER_VERDICT))
    host = get_host("mac-mini-m4")
    _run(peer_health.peer_health_summary(host))
    _run(peer_health.peer_health_summary(host))
    assert client.calls == 1

    peer_health.reset_cache()
    client = _wire(monkeypatch, TimeoutError("slow"))
    _run(peer_health.peer_health_summary(host))
    stored_until = peer_health._cache[host.id][0]
    assert stored_until - peer_health.time.monotonic() <= peer_health._TTL_FAILED_S + 1


def test_a_reachable_hub_peer_card_carries_its_health(monkeypatch):
    async def _reachable(host):
        return host.address

    async def _collect(host):
        return {"uptime_seconds": 42}

    async def _hub_up(host_id):
        return {"reachable": True, "local_git_sha": "a", "remote_git_sha": "a", "git_sha_match": True}

    monkeypatch.setattr(mc.remote_stats, "located_address", _reachable)
    monkeypatch.setattr(mc.remote_stats, "collect", _collect)
    monkeypatch.setattr(mc.remote_stats, "peer_health", _hub_up)
    monkeypatch.setattr(mc.sync_diagnosis, "diagnose", lambda **kw: {"kind": "in_sync", "sync_helps": False})
    _wire(monkeypatch, _Resp(200, PEER_VERDICT))
    card = _run(mc._probe_machine(get_host("mac-mini-m4"), "tower"))
    assert card["health"]["verdict"] == "act"


def test_a_peer_whose_hub_is_down_is_not_asked_for_health(monkeypatch):
    async def _reachable(host):
        return host.address

    async def _collect(host):
        return {"uptime_seconds": 42}

    async def _hub_down(host_id):
        return {"reachable": False, "local_git_sha": None, "remote_git_sha": None, "git_sha_match": None}

    async def _never(host):
        raise AssertionError("the health verdict must not be read from a hub that is down")

    monkeypatch.setattr(mc.remote_stats, "located_address", _reachable)
    monkeypatch.setattr(mc.remote_stats, "collect", _collect)
    monkeypatch.setattr(mc.remote_stats, "peer_health", _hub_down)
    monkeypatch.setattr(mc.peer_health, "peer_health_summary", _never)
    card = _run(mc._probe_machine(get_host("mac-mini-m4"), "tower"))
    assert card["health"] is None and card["hub"]["reachable"] is False
