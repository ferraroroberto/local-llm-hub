"""Unit tests for app_web/routers/_helpers.py's sse_stream() (issue #195) —
the shared subscribe -> seed -> disconnect/keepalive -> unsubscribe skeleton
now used by hub.py's log_tail/requests_stream and telemetry.py's
telemetry_stream, none of which had any direct test coverage before this
consolidation.

Drives the generator directly (not through TestClient/HTTP) with a fake
Request whose ``is_disconnected()`` flips true after N checks — this is
deterministic and can't hang on a real keepalive wait the way an
HTTP-level SSE test against an infinite stream would.
"""

from __future__ import annotations

import asyncio
import os
import threading

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

from app_web.routers._helpers import sse_pack, sse_stream
from src.async_fanout import AsyncFanout


def _run(coro, timeout: float = 10.0):
    """Run a coroutine on a fresh thread+loop (same pattern as
    tests/test_async_fanout.py / tests/test_services_router.py)."""
    bucket: dict = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            bucket["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001
            bucket["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise AssertionError("test scenario did not complete — sse_stream likely hung")
    if "error" in bucket:
        raise bucket["error"]
    return bucket.get("value")


class _FakeRequest:
    """``is_disconnected()`` returns False for the first ``n`` checks, then
    True — lets a scenario allow exactly ``n`` trips through the live-queue
    branch before the generator's loop exits."""

    def __init__(self, disconnect_after: int = 0) -> None:
        self._checks = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > self._disconnect_after


async def _collect(async_gen):
    return [chunk async for chunk in async_gen]


def test_sse_pack_formats_dicts_and_strings():
    assert sse_pack("plain") == "data: plain\n\n"
    assert sse_pack({"a": 1}) == 'data: {"a": 1}\n\n'
    assert sse_pack("x", event="ping") == "event: ping\ndata: x\n\n"


def test_sse_stream_yields_seed_then_stops_on_disconnect():
    async def scenario():
        fanout: AsyncFanout[str] = AsyncFanout(maxsize=10)
        fanout.attach_loop(asyncio.get_running_loop())
        request = _FakeRequest(disconnect_after=0)
        resp = sse_stream(request, fanout.subscribe, fanout.unsubscribe, seed=["line1", "line2"])
        chunks = await _collect(resp.body_iterator)
        assert chunks == ["data: line1\n\n", "data: line2\n\n"]

    _run(scenario())


def test_sse_stream_reverses_seed_when_requested():
    async def scenario():
        fanout: AsyncFanout[str] = AsyncFanout(maxsize=10)
        fanout.attach_loop(asyncio.get_running_loop())
        request = _FakeRequest(disconnect_after=0)
        resp = sse_stream(
            request, fanout.subscribe, fanout.unsubscribe,
            seed=["newest", "middle", "oldest"], reverse_seed=True,
        )
        chunks = await _collect(resp.body_iterator)
        assert chunks == ["data: oldest\n\n", "data: middle\n\n", "data: newest\n\n"]

    _run(scenario())


def test_sse_stream_applies_to_dict_to_live_items_only():
    async def scenario():
        fanout: AsyncFanout[dict] = AsyncFanout(maxsize=10)
        loop = asyncio.get_running_loop()
        fanout.attach_loop(loop)
        request = _FakeRequest(disconnect_after=1)  # allow one live-queue trip
        resp = sse_stream(
            request, fanout.subscribe, fanout.unsubscribe,
            seed=[{"already": "dict"}],
            to_dict=lambda raw: {"converted": raw},
            poll_timeout=2.0,
        )
        gen = resp.body_iterator
        chunks = []
        # Pull the seed frame, then push one live item before the generator's
        # next is_disconnected() check flips true and ends the loop.
        chunks.append(await gen.__anext__())
        fanout.push("raw-item")
        chunks.append(await gen.__anext__())
        # Loop ends on the next disconnect check; drain to confirm cleanup.
        rest = await _collect(gen)
        assert chunks == [
            'data: {"already": "dict"}\n\n',
            'data: {"converted": "raw-item"}\n\n',
        ]
        assert rest == []

    _run(scenario())


def test_sse_stream_unsubscribes_on_completion():
    async def scenario():
        fanout: AsyncFanout[str] = AsyncFanout(maxsize=10)
        fanout.attach_loop(asyncio.get_running_loop())
        request = _FakeRequest(disconnect_after=0)
        resp = sse_stream(request, fanout.subscribe, fanout.unsubscribe, seed=[])
        await _collect(resp.body_iterator)
        # No subscribers left after the generator's finally block ran.
        with fanout._lock:  # noqa: SLF001 — whitebox check, test-only
            assert len(fanout._subs) == 0

    _run(scenario())
