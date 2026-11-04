"""Name whatever blocks the hub's event loop.

Every admin poll, trivial or not, shares one asyncio loop, so one handler
that does blocking work for seconds shows up as a p95 spike on endpoints that
normally answer in a few milliseconds. From outside that looks like a slow
route; only the loop's own thread knows which call it is stuck in.

A daemon thread posts a heartbeat onto the loop every ``interval_s``. When the
last heartbeat is older than ``threshold_s`` the loop is blocked, and the
thread logs that thread's current stack once (the line it is stuck on *now*),
then logs how long the stall lasted when the heartbeat resumes. It runs off
the loop, so it keeps counting while the loop does not.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# A 10 Hz heartbeat keeps the wake-ups negligible. The 0.25 s threshold sits
# under the 0.3-0.6 s periodic stalls (a blocking subprocess spawn per peer poll)
# that a 0.5 s threshold never reported yet pushed the admin p95 over budget;
# ordinary handler work still stays well below it.
DEFAULT_INTERVAL_S = 0.1
DEFAULT_THRESHOLD_S = 0.25
_STACK_DEPTH = 12


def _stack_of(thread_id: int) -> str:
    frame = sys._current_frames().get(thread_id)  # noqa: SLF001 - the only way to see another thread's stack
    if frame is None:
        return "stack unavailable"
    return "".join(traceback.format_stack(frame, limit=_STACK_DEPTH)).rstrip()


class LoopWatch:
    def __init__(
        self,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        threshold_s: float = DEFAULT_THRESHOLD_S,
        clock: Callable[[], float] = time.monotonic,
        stack_of: Callable[[int], str] = _stack_of,
    ) -> None:
        self._interval_s = interval_s
        self._threshold_s = threshold_s
        self._clock = clock
        self._stack_of = stack_of
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread_id = 0
        self._last_beat = clock()
        self._stalled_since: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def beat(self) -> None:
        """Runs on the loop: proves it is turning, and closes an open stall."""
        now = self._clock()
        if self._stalled_since is not None:
            logger.warning("⚠️ event loop unblocked after %.1f s", now - self._stalled_since)
            self._stalled_since = None
        self._last_beat = now

    def check(self) -> None:
        """Runs on the watch thread: opens a stall once the heartbeat is overdue."""
        now = self._clock()
        overdue = now - self._last_beat
        if overdue < self._threshold_s or self._stalled_since is not None:
            return
        # Backdate to the last proof of life so the closing line reports the whole stall.
        self._stalled_since = self._last_beat
        logger.warning(
            "⚠️ event loop blocked for >%.1f s; it is stuck here:\n%s",
            overdue, self._stack_of(self._loop_thread_id),
        )

    def start(self) -> None:
        """Call from the loop's own thread, once."""
        if self._thread is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._last_beat = self._clock()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="loop-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def _run(self) -> None:
        loop = self._loop
        while not self._stop.wait(self._interval_s):
            try:
                loop.call_soon_threadsafe(self.beat)
            except RuntimeError:
                return  # the loop is closed: the hub is shutting down
            self.check()


WATCH = LoopWatch()
