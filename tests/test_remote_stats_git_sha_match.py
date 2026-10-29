"""``remote_stats.peer_health``'s ``git_sha_match`` tri-state (fleet-config#1558
local-llm-hub#906): ``None`` means "not compared" (either side's SHA is
missing or ``"unknown"``), distinct from an explicit ``False`` ("out of
sync") — ``machines.js`` renders the two states differently (`Build not
compared` vs `Out of sync`, attention tone), so folding a missing SHA into
``False`` would show a false alarm instead of "nothing to compare yet".
"""

from __future__ import annotations

import asyncio

import pytest

from src import host_profile
from src.fleet import remote_stats


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, version_body: dict, version_status: int = 200):
        self._version_body = version_body
        self._version_status = version_status

    async def get(self, url: str, timeout: float):
        if url.endswith("/health"):
            return _FakeResponse(200, {})
        return _FakeResponse(self._version_status, self._version_body)


class _FakeHost:
    dormant = False


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _peer_wiring(monkeypatch):
    monkeypatch.setattr(host_profile, "get_host", lambda host_id: _FakeHost())
    monkeypatch.setattr(remote_stats, "dial_address_async", lambda host: _async_ret("1.2.3.4"))
    monkeypatch.setattr(remote_stats, "hub_port", lambda: 8000)


async def _async_ret(value):
    return value


def _patch_client(monkeypatch, version_body, version_status=200):
    monkeypatch.setattr(
        remote_stats, "get_async_client",
        lambda: _FakeAsyncClient(version_body, version_status),
    )


def _patch_local_sha(monkeypatch, sha):
    monkeypatch.setattr("src.process.process_identity.git_sha", lambda: sha)


def test_missing_remote_sha_is_not_compared_not_out_of_sync(monkeypatch):
    _patch_local_sha(monkeypatch, "aaa1111")
    # A 4xx/5xx-shaped version response (or a hub too old to report one)
    # leaves remote_sha None.
    _patch_client(monkeypatch, {}, version_status=500)

    result = _run(remote_stats.peer_health("peer"))

    assert result["git_sha_match"] is None


def test_unknown_local_sha_is_not_compared_not_out_of_sync(monkeypatch):
    _patch_local_sha(monkeypatch, "unknown")
    _patch_client(monkeypatch, {"git_sha": "bbb2222"})

    result = _run(remote_stats.peer_health("peer"))

    assert result["git_sha_match"] is None


def test_matching_real_shas_compare_true(monkeypatch):
    _patch_local_sha(monkeypatch, "aaa1111")
    _patch_client(monkeypatch, {"git_sha": "aaa1111"})

    result = _run(remote_stats.peer_health("peer"))

    assert result["git_sha_match"] is True


def test_differing_real_shas_compare_false(monkeypatch):
    _patch_local_sha(monkeypatch, "aaa1111")
    _patch_client(monkeypatch, {"git_sha": "bbb2222"})

    result = _run(remote_stats.peer_health("peer"))

    assert result["git_sha_match"] is False
