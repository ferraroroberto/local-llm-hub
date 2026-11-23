"""Named thread pools for blocking work, one per class, so one slow class cannot starve the rest.

The event loop's shared default pool is one queue for everything: a burst of
slow SSH calls or a long backend start there delays a status read a page is
polling. Each class below gets its own bounded :class:`TimedExecutor`, which
also charges its queue and worker time to the request that submitted it
(``Server-Timing``, slow-request lines; ``src/request_timing.py``).

Sizes are upper bounds, not reservations: a ``ThreadPoolExecutor`` starts a
thread only when work arrives and none is idle, so an unused pool costs nothing.

:func:`run_on` is ``asyncio.to_thread`` with a pool named: it copies the
caller's context variables into the worker the same way.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable
from typing import Any, TypeVar

from ..request_timing import TimedExecutor

T = TypeVar("T")

# Status reads a page polls (listening ports, health probes, log tails, cached
# summaries): short, frequent, and the first thing a slow box makes feel stuck.
HOT_POLL = TimedExecutor("hot-poll", 8)
# Reads that walk a database or the disk to build a report: rare and slow.
HEAVY_READ = TimedExecutor("heavy-read", 4)
# Work a person or a request starts and waits on: backend start/stop, service
# start/stop, installs, config writes. A start can take minutes, so the pool is
# wide enough for every configured backend to start at once.
ACTION = TimedExecutor("action", 12)
# Calls to other machines (SSH, wake-on-LAN, peer reads): network-bound, slow
# when a peer is down, and fanned out across peers.
FAN_OUT = TimedExecutor("fan-out", 8)
# The hub's own background loops (watchers, samplers, failover, idle unload):
# nobody waits on them, so they must never take a thread a request needs.
BACKGROUND = TimedExecutor("background", 6)
# Audio and image work for an inference request (decode, transcription,
# generation): long per call and one per in-flight request.
MEDIA = TimedExecutor("media", 8)


async def run_on(pool: TimedExecutor, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run ``fn(*args, **kwargs)`` on ``pool`` and await its result, in a copy of the caller's context."""
    loop = asyncio.get_running_loop()
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return await loop.run_in_executor(pool, call)
