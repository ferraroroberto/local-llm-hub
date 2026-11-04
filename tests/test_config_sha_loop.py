"""``config_sha`` must not stall the event loop on a cache miss.

The sha shells out to ``git log``; the cache expires between a peer's 30 s
polls, so every poll of ``/admin/api/version`` and the Models listing paid the
spawn on the loop. A fake slow ``git`` makes the stall deterministic: no live
timing, only how long the loop goes without turning while the call is awaited.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from types import SimpleNamespace
from typing import List

import pytest

from app_web.routers import version as version_router
from src import config_write as cw
from tests._worker_loop import run_on_worker_thread as _run

_SLOW_GIT_S = 0.4
# Far below the spawn time yet far above scheduler jitter on a loaded box.
_MAX_LOOP_GAP_S = 0.2


@pytest.fixture
def slow_git(monkeypatch: pytest.MonkeyPatch) -> List[tuple]:
    calls: List[tuple] = []

    def fake_git(*args: str, **_kw: object) -> subprocess.CompletedProcess:
        calls.append(args)
        time.sleep(_SLOW_GIT_S)
        return subprocess.CompletedProcess(args, 0, stdout="abc1234\n", stderr="")

    monkeypatch.setattr(cw, "_git", fake_git)
    monkeypatch.setattr(cw, "_sha_cache", (0.0, ""))
    return calls


async def _longest_loop_gap(work) -> float:
    """Longest time the loop went without turning while ``work()`` ran."""
    gaps: List[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    task = asyncio.ensure_future(ticker())
    await asyncio.sleep(0.05)
    try:
        await work()
    finally:
        stop.set()
        await task
    return max(gaps)


def test_a_cache_miss_does_not_block_the_loop(slow_git: List[tuple]) -> None:
    async def scenario() -> float:
        return await _longest_loop_gap(cw.config_sha_async)

    assert _run(scenario(), timeout=10) < _MAX_LOOP_GAP_S
    assert len(slow_git) == 1


def test_the_blocking_form_does_stall_the_loop(slow_git: List[tuple]) -> None:
    """Guards the fixture: the sync call on the loop is what the fix removes."""

    async def scenario() -> float:
        async def work() -> None:
            cw.config_sha()

        return await _longest_loop_gap(work)

    assert _run(scenario(), timeout=10) >= _SLOW_GIT_S * 0.9


def test_a_fresh_cache_answers_without_spawning_git(slow_git: List[tuple]) -> None:
    async def scenario() -> List[str]:
        return [await cw.config_sha_async(), await cw.config_sha_async()]

    assert _run(scenario(), timeout=10) == ["abc1234", "abc1234"]
    assert len(slow_git) == 1


def test_a_failed_git_reads_unknown_not_a_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cw, "_git",
        lambda *a, **k: subprocess.CompletedProcess(a, 128, stdout="", stderr="fatal"),
    )
    monkeypatch.setattr(cw, "_sha_cache", (0.0, ""))

    assert _run(cw.config_sha_async(), timeout=10) == "unknown"


def test_the_version_route_does_not_block_the_loop(slow_git: List[tuple]) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(asset_hashes={})))

    async def scenario() -> float:
        async def work() -> None:
            body = await version_router.version(request)
            assert body["config_sha"] == "abc1234"

        return await _longest_loop_gap(work)

    assert _run(scenario(), timeout=10) < _MAX_LOOP_GAP_S
