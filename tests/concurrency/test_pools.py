"""``run_on`` keeps ``asyncio.to_thread``'s behaviour on a named pool, and charges the request."""

from __future__ import annotations

import asyncio
import contextvars
import threading

from src import request_timing
from src.concurrency.pools import BACKGROUND, HOT_POLL, run_on

_ITEM: contextvars.ContextVar[str] = contextvars.ContextVar("item", default="unset")


def test_run_on_runs_on_the_named_pool_with_args_and_kwargs() -> None:
    def work(a: int, *, b: int) -> tuple[int, str]:
        return a + b, threading.current_thread().name

    total, thread = asyncio.run(run_on(BACKGROUND, work, 2, b=3))

    assert total == 5
    assert thread.startswith("background")


def test_run_on_copies_the_callers_context_into_the_worker() -> None:
    async def main() -> str:
        _ITEM.set("from-caller")
        return await run_on(HOT_POLL, _ITEM.get)

    assert asyncio.run(main()) == "from-caller"


def test_run_on_charges_the_submitting_request() -> None:
    timing = request_timing.RequestTiming()

    async def main() -> None:
        request_timing.current.set(timing)
        await run_on(HOT_POLL, lambda: None)

    asyncio.run(main())

    assert timing.calls == 1
    assert timing.pool == "hot-poll"


def test_run_on_raises_the_workers_exception() -> None:
    def boom() -> None:
        raise ValueError("nope")

    try:
        asyncio.run(run_on(BACKGROUND, boom))
    except ValueError as exc:
        assert str(exc) == "nope"
    else:
        raise AssertionError("the worker's exception was swallowed")
