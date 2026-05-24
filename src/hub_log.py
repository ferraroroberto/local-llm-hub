"""In-memory log ring buffer shared between the hub and the /admin webapp.

A :class:`RingHandler` is attached to the root logger when the hub
starts. Every log record (also uvicorn's, by attaching to ``uvicorn``
and ``uvicorn.access``) goes into a thread-safe deque the admin's Hub
tab can poll or stream. This replaces the old Streamlit ``log_lines()``
+ ``Popen.stdout`` reader thread — in-process logging is enough now
that the hub *is* the process.

The ring is bounded (default 2000 lines) so an overnight-running hub
doesn't accumulate unbounded memory.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Deque, List, Optional, Set

RING_MAX = 2000

# Paths the admin SPA polls every few seconds. Logging every poll buries
# the actually-interesting traces (model calls, errors, startup) in
# self-noise. We drop them at the access-log layer only — the SPA's own
# live-request ring still reflects them when needed.
_POLLING_NOISE_PATHS = (
    "/admin/api/hub/status",
    "/admin/api/hub/counters",
    "/admin/api/hub/stats",
    "/admin/api/hub/requests/recent",
    "/admin/api/hub/errors/recent",
    "/admin/api/hub/log/recent",
    "/admin/api/models",
    "/admin/api/install/status",
    "/admin/api/version",
    "/admin/api/healthz",
    "/admin/api/playground/models",
    "/admin/api/webauthn/status",
)


def _is_polling_noise(msg: str) -> bool:
    """uvicorn.access lines look like
    ``127.0.0.1:51234 - "GET /admin/api/hub/stats HTTP/1.1" 200``. Match
    on the path substring — cheaper than parsing the full format.
    """
    return any(f' {p} ' in msg for p in _POLLING_NOISE_PATHS)


class RingHandler(logging.Handler):
    """Append formatted log lines to a bounded in-memory deque."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._ring: Deque[str] = deque(maxlen=RING_MAX)
        self._subs: Set["asyncio.Queue[str]"] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = ""
        # Drop the admin SPA's own polling chatter — every Hub tab fires
        # /admin/api/hub/{status,counters,stats,requests/recent,errors/recent}
        # every couple seconds, which would otherwise drown the log pane
        # in self-noise. The traces that operators *care* about — actual
        # /v1/messages calls, hub startup, errors — still land.
        if record.name == "uvicorn.access" and _is_polling_noise(msg):
            return
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001
            line = msg
        with self._lock:
            self._ring.append(line)
        self._fanout(line)

    def lines(self, limit: int = 400) -> List[str]:
        with self._lock:
            items = list(self._ring)[-limit:]
        return items

    # ------------------------------------------------------ live tail
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> "asyncio.Queue[str]":
        q: "asyncio.Queue[str]" = asyncio.Queue(maxsize=400)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: "asyncio.Queue[str]") -> None:
        with self._lock:
            self._subs.discard(q)

    def _fanout(self, line: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            subs = list(self._subs)
        if not subs:
            return

        def _push() -> None:
            for q in subs:
                try:
                    q.put_nowait(line)
                except asyncio.QueueFull:
                    pass

        try:
            loop.call_soon_threadsafe(_push)
        except RuntimeError:
            pass


# Module-level singleton — wired into the root logger from src/server.py.
HUB_LOG = RingHandler()


def install_root_handler() -> None:
    """Attach :data:`HUB_LOG` to root + relevant uvicorn loggers (idempotent)."""
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    HUB_LOG.setFormatter(fmt)

    targets = ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    for name in targets:
        lg = logging.getLogger(name)
        if HUB_LOG not in lg.handlers:
            lg.addHandler(HUB_LOG)
