"""Thread-safe asyncio pub/sub fan-out for SSE-style subscribers.

Extracted from :mod:`src.hub_observability` (``Observatory``) and
:mod:`src.hub_log` (``RingHandler``) — both carried a line-for-line
identical subscribe/unsubscribe/fan-out implementation (same lock
discipline, same ``call_soon_threadsafe``/``QueueFull`` handling),
differing only in the queue's ``maxsize`` and item type (issue #195).

The write side (``push``) is called from a synchronous context — a log
handler's ``emit()``, or a plain method on a request-ring class — that
doesn't own the running event loop. ``attach_loop()`` captures it once at
startup so ``push()`` can hop onto the loop via ``call_soon_threadsafe``
from any thread.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Generic, List, Optional, Set, TypeVar

T = TypeVar("T")


class AsyncFanout(Generic[T]):
    """Owns its own lock, independent of whatever lock the composing class
    uses for its other state — subscribe/unsubscribe/push never need to be
    held together with a caller's ring-buffer or counters lock.
    """

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._subs: Set["asyncio.Queue[T]"] = set()
        # Captured lazily so we don't fight with uvicorn's event-loop
        # selection at import time.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running event loop so non-async callers can fan
        out without grabbing the loop themselves."""
        self._loop = loop

    def subscribe(self) -> "asyncio.Queue[T]":
        q: "asyncio.Queue[T]" = asyncio.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: "asyncio.Queue[T]") -> None:
        with self._lock:
            self._subs.discard(q)

    def push(self, item: T) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            subs: List["asyncio.Queue[T]"] = list(self._subs)
        if not subs:
            return

        def _push() -> None:
            for q in subs:
                try:
                    q.put_nowait(item)
                except asyncio.QueueFull:
                    pass

        try:
            loop.call_soon_threadsafe(_push)
        except RuntimeError:
            # Loop closed in the gap — drop the event.
            pass
