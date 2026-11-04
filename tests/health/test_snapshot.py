"""Snapshot: a polled read is answered from memory, rebuilt off the request path.

Deterministic: a gated fake probe stands in for a slow ``docker info`` and a
list cell stands in for the clock, so nothing here depends on real timing.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from src.health.snapshot import Snapshot
from tests._worker_loop import run_on_worker_thread as _run


class _SlowProbe:
    """A build that waits on a gate the test opens, and counts its starts."""

    def __init__(self) -> None:
        self.starts = 0
        self.gate = asyncio.Event()
        self.value = "v1"

    async def __call__(self) -> Dict[str, Any]:
        self.starts += 1
        value = self.value
        await self.gate.wait()
        return {"value": value}


async def _settle() -> None:
    """Let every ready task run; the fake probe has no real I/O to wait on."""
    for _ in range(5):
        await asyncio.sleep(0)


def _snapshot(probe: _SlowProbe, now: List[float]) -> Snapshot:
    return Snapshot(probe, fresh_s=2.0, max_age_s=20.0, clock=lambda: now[0])


def test_first_read_builds_inline():
    async def scenario():
        probe, now = _SlowProbe(), [0.0]
        snap = _snapshot(probe, now)
        read = asyncio.ensure_future(snap.get())
        await _settle()
        assert not read.done(), "nothing stored yet, so the read waits for the build"
        probe.gate.set()
        return await read

    assert _run(scenario(), timeout=5) == {"value": "v1", "snapshot_age_s": 0.0}


def test_fresh_read_reuses_the_build():
    async def scenario():
        probe, now = _SlowProbe(), [0.0]
        probe.gate.set()
        snap = _snapshot(probe, now)
        await snap.get()
        now[0] += 1.9
        body = await snap.get()
        return probe.starts, body

    starts, body = _run(scenario(), timeout=5)
    assert starts == 1
    assert body["snapshot_age_s"] == 1.9


def test_aging_read_returns_at_once_while_a_slow_probe_rebuilds():
    """The poll must not wait on the probe: it gets the old build, aged."""

    async def scenario():
        probe, now = _SlowProbe(), [0.0]
        probe.gate.set()
        snap = _snapshot(probe, now)
        await snap.get()
        probe.gate.clear()
        probe.value = "v2"
        now[0] += 5.0
        stale = await asyncio.wait_for(snap.get(), timeout=5)  # hang guard only
        await _settle()
        started_while_gated = probe.starts
        again = await snap.get()  # still gated: must not start a second rebuild
        probe.gate.set()
        await _settle()
        now[0] += 0.5
        fresh = await snap.get()
        return stale, started_while_gated, probe.starts, again, fresh

    stale, started, starts_after_second_read, again, fresh = _run(scenario(), timeout=5)
    assert stale == {"value": "v1", "snapshot_age_s": 5.0}
    assert started == 2 and starts_after_second_read == 2, "one rebuild in flight, shared"
    assert again["value"] == "v1"
    assert fresh["value"] == "v2" and fresh["snapshot_age_s"] == 0.5


def test_read_past_the_staleness_bound_rebuilds_inline():
    async def scenario():
        probe, now = _SlowProbe(), [0.0]
        probe.gate.set()
        snap = _snapshot(probe, now)
        await snap.get()
        probe.gate.clear()
        probe.value = "v2"
        now[0] += 20.0
        read = asyncio.ensure_future(snap.get())
        await _settle()
        waited = not read.done()
        probe.gate.set()
        return waited, await read

    waited, body = _run(scenario(), timeout=5)
    assert waited, "a build this old is never served as current"
    assert body == {"value": "v2", "snapshot_age_s": 0.0}


def test_mark_dirty_forces_an_inline_rebuild():
    async def scenario():
        probe, now = _SlowProbe(), [0.0]
        probe.gate.set()
        snap = _snapshot(probe, now)
        await snap.get()
        probe.value = "v2"
        snap.mark_dirty()
        return await snap.get()

    assert _run(scenario(), timeout=5)["value"] == "v2"


def test_rebuild_started_before_a_write_does_not_clear_it():
    """A background rebuild that overlaps mark_dirty() must not be stored."""

    async def scenario():
        probe, now = _SlowProbe(), [0.0]
        probe.gate.set()
        snap = _snapshot(probe, now)
        await snap.get()
        probe.gate.clear()
        now[0] += 5.0
        await snap.get()  # starts a background rebuild, still gated on the old state
        await _settle()
        snap.mark_dirty()  # the write lands while it runs
        probe.value = "after-write"
        probe.gate.set()
        await _settle()
        return await snap.get()

    assert _run(scenario(), timeout=5)["value"] == "after-write"


def test_failed_background_rebuild_keeps_the_last_build():
    async def scenario():
        now = [0.0]
        calls = [0]

        async def build() -> Dict[str, Any]:
            calls[0] += 1
            if calls[0] == 2:
                raise RuntimeError("probe blew up")
            return {"value": f"v{calls[0]}"}

        snap = Snapshot(build, fresh_s=2.0, max_age_s=20.0, clock=lambda: now[0])
        await snap.get()
        now[0] += 5.0
        stale = await snap.get()  # background rebuild raises
        await _settle()
        now[0] += 1.0
        retry = await snap.get()  # next aging read tries again
        await _settle()
        return stale, retry, calls[0]

    stale, retry, calls = _run(scenario(), timeout=5)
    assert stale["value"] == "v1"
    assert retry["value"] == "v1"
    assert calls == 3
