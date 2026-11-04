"""LoopWatch: a blocked event loop is logged once, with the stack it is stuck in.

The detection runs on a fake clock and a fake stack reader, so the unit tests
need no real waiting; one test blocks a real loop to prove the stack names the
blocking call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List

import pytest

from src.health.loop_watch import LoopWatch
from tests._worker_loop import run_on_worker_thread as _run


def _watch(now: List[float]) -> LoopWatch:
    return LoopWatch(threshold_s=0.5, clock=lambda: now[0], stack_of=lambda _tid: "FAKE STACK")


def test_a_turning_loop_is_never_reported(caplog: pytest.LogCaptureFixture) -> None:
    now = [0.0]
    watch = _watch(now)
    with caplog.at_level(logging.WARNING, logger="src.health.loop_watch"):
        for _ in range(10):
            now[0] += 0.4
            watch.check()
            watch.beat()
    assert caplog.records == []


def test_a_stall_is_logged_once_with_the_stack_then_closed(caplog: pytest.LogCaptureFixture) -> None:
    now = [0.0]
    watch = _watch(now)
    with caplog.at_level(logging.WARNING, logger="src.health.loop_watch"):
        now[0] = 0.6
        watch.check()
        now[0] = 2.0
        watch.check()  # still the same stall: no second stack
        now[0] = 3.0
        watch.beat()
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert "event loop blocked" in messages[0] and "FAKE STACK" in messages[0]
    assert "unblocked after 3.0 s" in messages[1]


def test_a_second_stall_is_reported_again(caplog: pytest.LogCaptureFixture) -> None:
    now = [0.0]
    watch = _watch(now)
    with caplog.at_level(logging.WARNING, logger="src.health.loop_watch"):
        for start in (0.0, 10.0):
            now[0] = start + 0.6
            watch.check()
            now[0] = start + 1.0
            watch.beat()
    assert sum("event loop blocked" in r.getMessage() for r in caplog.records) == 2


def _block_the_loop_here() -> None:
    time.sleep(0.5)


def test_the_logged_stack_names_the_blocking_call(caplog: pytest.LogCaptureFixture) -> None:
    watch = LoopWatch(interval_s=0.02, threshold_s=0.15)

    async def scenario() -> None:
        watch.start()
        try:
            await asyncio.sleep(0.1)
            _block_the_loop_here()
            await asyncio.sleep(0.1)
        finally:
            watch.stop()

    with caplog.at_level(logging.WARNING, logger="src.health.loop_watch"):
        _run(scenario(), timeout=10)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "_block_the_loop_here" in text
    assert "unblocked after" in text
