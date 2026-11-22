"""Demand-paced live stats: the sampler's cadence, its wake-up and the no-spawn stats read.

The cadence tests drive a fake clock; the wake-up tests use real asyncio sleeps
bounded far below the idle interval, so a missed wake shows as a timeout.
"""

from __future__ import annotations

import asyncio
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from src.health import live_stats


@pytest.fixture(autouse=True)
def _reset_live():
    from src.health.live_stats import LIVE

    yield
    LIVE.record_gpus([])
    LIVE._last_demand = None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_unwatched_hub_samples_at_the_idle_cadence():
    clock = Clock()
    live = live_stats.LiveStats(clock)
    assert not live.watched()
    assert live.interval_s() == live_stats.IDLE_INTERVAL_S
    assert live_stats.IDLE_INTERVAL_S >= 10 * live_stats.LIVE_INTERVAL_S


def test_a_stats_read_holds_the_live_cadence_for_the_demand_window():
    clock = Clock()
    live = live_stats.LiveStats(clock)
    live.note_demand()
    assert live.interval_s() == live_stats.LIVE_INTERVAL_S
    clock.now += live_stats.DEMAND_WINDOW_S - 0.1
    assert live.watched()
    clock.now += 0.2
    assert not live.watched()
    assert live.interval_s() == live_stats.IDLE_INTERVAL_S


def test_gpus_is_the_newest_sample_and_empty_before_the_first():
    live = live_stats.LiveStats(Clock())
    assert live.gpus() == []
    live.record_gpus([{"name": "gpu0", "vram_percent": 40.0}])
    live.record_gpus([{"name": "gpu0", "vram_percent": 41.0}])
    assert live.gpus() == [{"name": "gpu0", "vram_percent": 41.0}]


def test_demand_cuts_an_idle_sleep_short():
    live = live_stats.LiveStats()

    async def scenario() -> float:
        started = time.monotonic()
        sleeper = asyncio.create_task(live.sleep(live_stats.IDLE_INTERVAL_S))
        await asyncio.sleep(0.05)
        live.note_demand()
        await asyncio.wait_for(sleeper, timeout=2.0)
        return time.monotonic() - started

    assert asyncio.run(scenario()) < 2.0


def test_a_poll_inside_the_window_does_not_wake_the_live_sleep():
    """Waking on every poll would add one sample per poll on top of the live cadence."""
    live = live_stats.LiveStats()
    live.note_demand()

    async def scenario() -> bool:
        sleeper = asyncio.create_task(live.sleep(0.5))
        await asyncio.sleep(0.05)
        live.note_demand()
        await asyncio.sleep(0.1)
        return sleeper.done()

    assert asyncio.run(scenario()) is False


def test_hub_stats_starts_no_process(monkeypatch):
    from src import server as server_mod
    from src import system_stats
    from src.health.live_stats import LIVE

    def no_spawn(*args, **kwargs):
        raise AssertionError(f"/api/hub/stats started a process: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    monkeypatch.setattr(system_stats, "gpu_stats", no_spawn)
    monkeypatch.setattr(LIVE, "_last_demand", None)
    LIVE.record_gpus([{"name": "gpu0", "used_mb": 1.0, "total_mb": 2.0, "vram_percent": 50.0,
                       "util_percent": 7.0}])

    r = TestClient(server_mod.app).get("/admin/api/hub/stats")

    assert r.status_code == 200, r.text
    assert r.json()["gpus"][0]["vram_percent"] == 50.0
    assert LIVE.watched()

