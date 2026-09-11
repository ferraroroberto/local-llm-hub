"""Run a coroutine on a fresh event loop in a worker thread.

``asyncio.run()`` (and ``loop.run_until_complete()`` on the main thread)
raise when an outer loop is already running — which happens in this suite
once the Playwright e2e tests have started one (see ``conftest.py``'s
``pytest_collection_modifyitems``), making such tests flaky in
isolation-vs-suite ordering. Running on a worker thread guarantees a clean
asyncio context.

One shared copy (#556): seven test modules used to carry their own,
each docstring noting it mirrored the others.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable, Optional


def run_on_worker_thread(
    coro: Awaitable[Any],
    timeout: Optional[float] = None,
    loop_factory: Callable[[], asyncio.AbstractEventLoop] = asyncio.new_event_loop,
) -> Any:
    """Run ``coro`` to completion on a new loop in a worker thread.

    Returns its result, re-raising whatever it raised. With ``timeout``, a
    scenario still running after that many seconds fails the test instead of
    hanging the suite (the thread is a daemon, so it can't block exit).
    ``loop_factory`` pins the loop class, e.g. ``asyncio.SelectorEventLoop``.
    """
    bucket: dict = {}

    def _worker() -> None:
        loop = loop_factory()
        try:
            bucket["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised in caller
            bucket["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise AssertionError(f"coroutine did not complete within {timeout}s — likely hung")
    if "error" in bucket:
        raise bucket["error"]
    return bucket.get("value")
