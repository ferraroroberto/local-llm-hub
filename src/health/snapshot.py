"""A polled read answered from memory, rebuilt off the request path.

The Hub tab polls ``/admin/api/services/status`` every few seconds, and
building it spawns ``docker info`` (~250 ms) plus two HTTP probes. A fixed
TTL as long as the poll spacing never hits: each poll lands just past it.
:class:`Snapshot` instead serves the last build at once and refreshes it in
the background, so a poll never waits on a slow probe:

* a read older than ``fresh_s`` returns the stored build and starts one
  background rebuild; an unwatched endpoint rebuilds nothing;
* a read older than ``max_age_s`` (or with nothing stored, or after
  :meth:`mark_dirty`) rebuilds inline, so a stale build is never served as
  current and a control's write is never hidden behind the old state;
* every answer carries ``snapshot_age_s``, how old its build is.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

Build = Callable[[], Awaitable[Dict[str, Any]]]


class Snapshot:
    def __init__(
        self,
        build: Build,
        *,
        fresh_s: float,
        max_age_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._build = build
        self._fresh_s = fresh_s
        self._max_age_s = max_age_s
        self._clock = clock
        self._stored: Optional[Tuple[float, int, Dict[str, Any]]] = None  # (built_at, seq, body)
        # Bumped by mark_dirty(); a build that started under an older number
        # is stale on arrival, so an overlapping rebuild cannot un-dirty it.
        self._seq = 0
        self._inflight: Optional[Tuple[int, "asyncio.Task[Dict[str, Any]]"]] = None

    def mark_dirty(self) -> None:
        """The next read rebuilds inline: a write just changed what it shows."""
        self._seq += 1

    def reset(self) -> None:
        self._stored = None
        self._inflight = None
        self._seq += 1

    def _refresh(self) -> "asyncio.Task[Dict[str, Any]]":
        """The rebuild for the current ``_seq``, shared by every concurrent reader."""
        inflight = self._inflight
        if (
            inflight is not None
            and inflight[0] == self._seq
            and not inflight[1].done()
            # A task belongs to the loop that made it; a dead loop's never finishes.
            and inflight[1].get_loop() is asyncio.get_running_loop()
        ):
            return inflight[1]
        seq = self._seq

        async def _run() -> Dict[str, Any]:
            body = await self._build()
            if seq == self._seq:
                self._stored = (self._clock(), seq, body)
            return body

        task = asyncio.ensure_future(_run())
        task.add_done_callback(self._log_failure)
        self._inflight = (seq, task)
        return task

    @staticmethod
    def _log_failure(task: "asyncio.Task[Dict[str, Any]]") -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("⚠️ snapshot rebuild failed: %r", task.exception())

    async def get(self) -> Dict[str, Any]:
        stored = self._stored
        now = self._clock()
        if stored is not None and stored[1] == self._seq and now - stored[0] < self._max_age_s:
            if now - stored[0] >= self._fresh_s:
                self._refresh()
            return {**stored[2], "snapshot_age_s": round(now - stored[0], 1)}
        body = await self._refresh()
        return {**body, "snapshot_age_s": 0.0}
