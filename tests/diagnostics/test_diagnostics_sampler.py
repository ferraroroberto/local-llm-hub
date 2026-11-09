"""Sampler stop semantics with the tick and the grace window injected.

A stop that outlives the grace window falls back to cancelling the capture
task, but cancelling an ``asyncio.to_thread`` await never stops the worker
thread. These tests hold that worker at a chosen point with a threading event
the test controls, so the race is reproduced deterministically rather than by
loading the CPU until a real tick runs long."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from src.diagnostics import sampler, store

_WAIT_S = 5.0


def _sample() -> store.SystemSample:
    return store.SystemSample(
        ts=time.time(), cpu_percent=10.0, per_core=[10.0], load_avg=None,
        ram={"used_gb": 4.0, "total_gb": 16.0, "percent": 25.0},
        swap={"used_gb": 0.0, "total_gb": 8.0, "percent": 0.0},
        disk={"used_gb": 10.0, "total_gb": 500.0, "percent": 2.0},
        disk_io={}, net_io={}, gpus=[], process_count=0,
    )


@pytest.fixture()
def slow_world(tmp_path, monkeypatch):
    """Isolated store, no CPU priming, and a grace window far shorter than
    the tick the test is about to hold open."""
    store.set_db_path(tmp_path / "diag.db")
    monkeypatch.setattr(sampler.attribution, "prime_cpu_percent", lambda: None)
    monkeypatch.setattr(sampler, "_STOP_GRACE_S", 0.1)

    finished = threading.Event()
    real_write_tick = sampler._write_tick

    def _tracked_write_tick(run_id):
        try:
            return real_write_tick(run_id)
        finally:
            finished.set()

    monkeypatch.setattr(sampler, "_write_tick", _tracked_write_tick)
    yield finished
    store.set_db_path(None)


async def _start_and_wait(entered: threading.Event) -> str:
    await sampler.start_run(interval_s=5, duration_s=600)
    run_id = sampler.active_run().run_id
    assert await asyncio.to_thread(entered.wait, _WAIT_S), "the tick never started"
    return run_id


def _assert_closed(run_id: str) -> None:
    run = store.get_run(run_id)
    assert run["status"] == "stopped"
    assert run["verdict_level"] is not None, "the finalize (verdict) was skipped"


def test_tick_still_collecting_after_stop_never_writes(slow_world, monkeypatch):
    """The worker is mid-collection when the grace window runs out: its
    sample must be dropped, not written after stop returned."""
    finished = slow_world
    entered, release = threading.Event(), threading.Event()

    def _slow_collect():
        entered.set()
        release.wait(_WAIT_S)
        return _sample(), [], [], False

    monkeypatch.setattr(sampler, "_collect_tick", _slow_collect)

    async def scenario():
        run_id = await _start_and_wait(entered)
        assert (await sampler.stop_run())["stopped"] is True
        settled = len(store.samples(run_id))
        release.set()
        assert await asyncio.to_thread(finished.wait, _WAIT_S), "the worker never finished"
        return run_id, settled

    run_id, settled = asyncio.run(scenario())
    assert settled == 0
    assert len(store.samples(run_id)) == settled, "a write landed after stop returned"
    _assert_closed(run_id)


def test_tick_mid_write_at_stop_completes_before_stop_returns(slow_world, monkeypatch):
    """The worker is inside the store write when the grace window runs out:
    stop must wait for that write, so the row count it leaves is final."""
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(sampler, "_collect_tick", lambda: (_sample(), [], [], False))
    real_write = store.write_sample

    def _slow_write(*args, **kwargs):
        entered.set()
        release.wait(_WAIT_S)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(store, "write_sample", _slow_write)

    async def scenario():
        run_id = await _start_and_wait(entered)
        # Released from another thread while stop is waiting on the write.
        threading.Timer(0.3, release.set).start()
        assert (await sampler.stop_run())["stopped"] is True
        settled = len(store.samples(run_id))
        await asyncio.sleep(0.5)
        return run_id, settled

    run_id, settled = asyncio.run(scenario())
    assert settled == 1, "stop returned before the in-flight write finished"
    assert len(store.samples(run_id)) == settled
    _assert_closed(run_id)
