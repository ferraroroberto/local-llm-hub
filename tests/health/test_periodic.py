"""The shared background-loop skeleton: paced by the interval, never ended by a failed tick."""

import asyncio
import logging
from typing import Any, Dict, List

from src.health import periodic


def _run_ticks(monkeypatch, tick, *, stop_after: int, interval: float = 100.0, floor: float = 1.0, **kwargs):
    """Drive ``run_ticks`` until ``stop_after`` sleeps, returning each requested sleep."""
    sleeps: List[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= stop_after:
            raise asyncio.CancelledError

    monkeypatch.setattr(periodic.asyncio, "sleep", fake_sleep)
    cfg = {"interval_s": interval}

    async def scenario():
        await periodic.run_ticks("test watch", cfg, load_cfg=lambda: cfg, tick=tick,
                                 interval_of=lambda c: c["interval_s"], floor_s=floor, **kwargs)

    try:
        asyncio.run(scenario())
    except asyncio.CancelledError:
        pass
    return sleeps


def test_a_failed_tick_is_logged_and_the_loop_keeps_going(monkeypatch, caplog):
    calls: List[int] = []

    def tick(_cfg: Dict[str, Any]) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger=periodic.logger.name):
        sleeps = _run_ticks(monkeypatch, tick, stop_after=2)
    assert len(calls) == 2 and len(sleeps) == 2
    assert "test watch: sample failed: boom" in caplog.text


def test_the_sleep_is_the_interval_less_the_tick_but_never_below_the_floor(monkeypatch):
    sleeps = _run_ticks(monkeypatch, lambda _c: None, stop_after=1, interval=100.0)
    assert 99.0 < sleeps[0] <= 100.0
    assert _run_ticks(monkeypatch, lambda _c: None, stop_after=1, interval=0.0, floor=7.0) == [7.0]


def test_on_result_sees_first_only_on_the_first_success(monkeypatch):
    seen: List[Any] = []

    def tick(_cfg: Dict[str, Any]) -> int:
        if not seen:
            seen.append("failed once")
            raise RuntimeError("first pass fails")
        return len(seen)

    _run_ticks(monkeypatch, tick, stop_after=3, on_result=lambda out, first: seen.append((out, first)))
    assert seen[1:] == [(1, True), (2, False)]


def test_a_disabled_watcher_returns_without_ticking(monkeypatch, caplog):
    loaded: List[Dict[str, Any]] = []

    async def scenario():
        await periodic.run_periodic(
            "test watch", load_cfg=lambda: {"on": False}, tick=lambda _c: loaded.append("ticked"),
            interval_of=lambda c: 1, floor_s=1.0, enabled_of=lambda c: c["on"], boot_delay_of=lambda c: 0,
            disabled_in="config/x.json", on_loaded=loaded.append)

    with caplog.at_level(logging.INFO, logger=periodic.logger.name):
        asyncio.run(scenario())
    assert loaded == [{"on": False}]
    assert "test watch: disabled in config/x.json" in caplog.text
