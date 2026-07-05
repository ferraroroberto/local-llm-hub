"""Unit tests for src/async_fanout.py's AsyncFanout (issue #195).

Extracted from Observatory (hub_observability.py) and RingHandler
(hub_log.py) — both previously carried an identical, untested
subscribe/unsubscribe/fan-out implementation inline. These tests exercise
the shared helper directly since neither original class had any coverage
of this behavior before the extraction.
"""

from __future__ import annotations

import asyncio
import threading

from src.async_fanout import AsyncFanout


def _run(coro):
    """Run a coroutine on a fresh thread+loop — mirrors the pattern already
    used in tests/test_services_router.py, so this suite doesn't fight an
    already-running loop from elsewhere in the session."""
    bucket: dict = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            bucket["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001
            bucket["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_worker)
    t.start()
    t.join(timeout=10)
    if "error" in bucket:
        raise bucket["error"]
    return bucket.get("value")


def test_push_delivers_to_all_subscribers():
    async def scenario():
        fanout: AsyncFanout[str] = AsyncFanout(maxsize=10)
        fanout.attach_loop(asyncio.get_running_loop())
        q1 = fanout.subscribe()
        q2 = fanout.subscribe()

        fanout.push("hello")
        await asyncio.sleep(0.05)  # let call_soon_threadsafe's callback run

        assert q1.get_nowait() == "hello"
        assert q2.get_nowait() == "hello"

    _run(scenario())


def test_unsubscribe_stops_delivery():
    async def scenario():
        fanout: AsyncFanout[str] = AsyncFanout(maxsize=10)
        fanout.attach_loop(asyncio.get_running_loop())
        q1 = fanout.subscribe()
        q2 = fanout.subscribe()
        fanout.unsubscribe(q2)

        fanout.push("world")
        await asyncio.sleep(0.05)

        assert q1.get_nowait() == "world"
        assert q2.empty()

    _run(scenario())


def test_queue_full_is_swallowed_not_raised():
    async def scenario():
        fanout: AsyncFanout[str] = AsyncFanout(maxsize=1)
        fanout.attach_loop(asyncio.get_running_loop())
        q = fanout.subscribe()

        fanout.push("first")
        fanout.push("second")  # queue already full after "first" lands
        await asyncio.sleep(0.05)

        # No exception propagated; exactly the first item made it through.
        assert q.get_nowait() == "first"
        assert q.empty()

    _run(scenario())


def test_push_before_attach_loop_is_a_noop():
    async def scenario():
        fanout: AsyncFanout[str] = AsyncFanout(maxsize=10)
        q = fanout.subscribe()
        fanout.push("too early")  # no loop attached yet — must not raise
        await asyncio.sleep(0.01)
        assert q.empty()

    _run(scenario())


def test_push_with_no_subscribers_is_a_noop():
    async def scenario():
        fanout: AsyncFanout[str] = AsyncFanout(maxsize=10)
        fanout.attach_loop(asyncio.get_running_loop())
        fanout.push("nobody listening")  # must not raise
        await asyncio.sleep(0.01)

    _run(scenario())
