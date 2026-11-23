"""Demand-paced live stats: the Hub tab's RAM/CPU/GPU sampler and its latest snapshot.

Only the Hub tab's sparklines read these samples, so the sampler runs at the
live cadence only while a viewer has asked for stats recently, and at the idle
cadence otherwise; each sample shells out to ``nvidia-smi``, so an unwatched
hub would otherwise start a process every two seconds for nobody. A stats read
records demand and wakes a sleeping sampler, so the viewer's next poll is fresh.
``/api/hub/stats`` answers from ``gpus()`` instead of spawning its own probe; the
Machines card and the fleet-placement read answer from ``fresh_gpus()``, which serves the
same sample without asking for the live cadence.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable, Dict, List, Optional

# A viewer watching: fine enough that the sparklines look live.
LIVE_INTERVAL_S = 2.0
# Nobody watching: the history keeps a coarse trace at two nvidia-smi spawns a minute.
IDLE_INTERVAL_S = 30.0
# A viewer counts as watching this long after its last read: the Hub tab polls every
# 2.5 s while visible and stops in a hidden tab, so a few missed polls end the window.
DEMAND_WINDOW_S = 15.0
# Oldest sample a card may show: two idle intervals, so one slow or failed probe leaves the
# card on the previous reading while a stopped sampler blanks it rather than showing a
# frozen figure as live.
GPU_MAX_AGE_S = 2 * IDLE_INTERVAL_S


class LiveStats:
    """The demand window and the newest GPU snapshot. Thread-safe: the sampler
    records from a worker thread, request handlers read on the event loop."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._last_demand: Optional[float] = None
        self._gpus: Optional[List[Dict[str, Any]]] = None
        self._gpus_at: Optional[float] = None
        self._wake: Optional[asyncio.Event] = None

    def note_demand(self) -> None:
        """A viewer read the stats: keep the live cadence, and cut an idle sleep short when
        this read starts the window. Call on the event loop that runs the sampler."""
        was_watched = self.watched()
        with self._lock:
            self._last_demand = self._clock()
        # Only the idle-to-watched edge wakes the sampler: waking it on every poll
        # would sample once per poll on top of the live cadence.
        if not was_watched and self._wake is not None:
            self._wake.set()

    def watched(self) -> bool:
        with self._lock:
            last = self._last_demand
        return last is not None and self._clock() - last < DEMAND_WINDOW_S

    def interval_s(self) -> float:
        return LIVE_INTERVAL_S if self.watched() else IDLE_INTERVAL_S

    def record_gpus(self, gpus: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._gpus = list(gpus)
            self._gpus_at = self._clock()

    def gpus(self) -> List[Dict[str, Any]]:
        """The newest sampled GPU list; empty until the first sample, never a new probe."""
        with self._lock:
            return list(self._gpus or [])

    def fresh_gpus(self, max_age_s: float = GPU_MAX_AGE_S) -> List[Dict[str, Any]]:
        """The newest sample when it is at most ``max_age_s`` old, else empty (the "no GPU
        reading" shape every card already handles). Records no demand: a card that polls
        slowly must not pull the sampler up to the live cadence."""
        with self._lock:
            sampled_at = self._gpus_at
            gpus = list(self._gpus or [])
        if sampled_at is None or self._clock() - sampled_at > max_age_s:
            return []
        return gpus

    async def sleep(self, seconds: float) -> None:
        """Sleep ``seconds``, or less when demand arrives while idle."""
        # A fresh event per sleep: an asyncio.Event binds to the loop it first waits on.
        wake = self._wake = asyncio.Event()
        try:
            await asyncio.wait_for(wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            self._wake = None


LIVE = LiveStats()
