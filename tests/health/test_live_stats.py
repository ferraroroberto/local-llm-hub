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



def test_fresh_gpus_serves_the_sample_inside_the_freshness_bound_only():
    clock = Clock()
    live = live_stats.LiveStats(clock)
    assert live.fresh_gpus() == []
    live.record_gpus([{"name": "gpu0", "vram_percent": 40.0}])
    clock.now += live_stats.GPU_MAX_AGE_S - 0.1
    assert live.fresh_gpus() == [{"name": "gpu0", "vram_percent": 40.0}]
    clock.now += 0.2
    assert live.fresh_gpus() == []


def test_the_freshness_bound_outlasts_an_idle_sleep_and_a_slow_probe():
    """A card must not blank between two idle samples, or when one probe runs long."""
    assert live_stats.GPU_MAX_AGE_S >= 2 * live_stats.IDLE_INTERVAL_S


def test_a_cached_gpu_read_neither_wakes_nor_holds_the_live_cadence():
    """The Machines card polls every 10 s: letting it demand live sampling would
    start nvidia-smi every 2 s, five times the rate its own reads used to."""
    live = live_stats.LiveStats(Clock())
    live.record_gpus([{"name": "gpu0"}])
    live.fresh_gpus()
    assert not live.watched()
    assert live.interval_s() == live_stats.IDLE_INTERVAL_S


def _no_spawn(monkeypatch):
    from src import system_stats

    def refuse(*args, **kwargs):
        raise AssertionError(f"a request started a process: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(system_stats, "gpu_stats", refuse)


def test_machines_self_card_starts_no_process_and_shows_the_cached_sample(monkeypatch):
    from src.fleet import machine_console
    from src.health.live_stats import LIVE

    _no_spawn(monkeypatch)
    sample = {"name": "gpu0", "used_mb": 1.0, "total_mb": 2.0, "vram_percent": 50.0,
              "util_percent": 7.0}
    LIVE.record_gpus([sample])

    snap = asyncio.run(machine_console.self_snapshot())

    assert snap["gpus"] == [sample]


def test_machines_self_card_is_empty_when_the_cached_sample_is_stale(monkeypatch):
    from src.fleet import machine_console
    from src.health.live_stats import LIVE

    _no_spawn(monkeypatch)
    LIVE.record_gpus([{"name": "gpu0", "total_mb": 2.0}])
    monkeypatch.setattr(LIVE, "_gpus_at", LIVE._clock() - live_stats.GPU_MAX_AGE_S - 1)

    assert asyncio.run(machine_console.self_snapshot())["gpus"] == []

