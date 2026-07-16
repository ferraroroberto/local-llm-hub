"""Unit test for the shared sync httpx client's lazy-singleton guard.

Regression for issue #297: `get_sync_client()` is called from real OS
threads in a threadpool (unlike `get_async_client()`, which never awaits
between check and assignment). An unlocked check-then-construct let two
concurrent first-callers both build an `httpx.Client`, leaking the loser's
connection pool.
"""

from __future__ import annotations

import threading

from src import http_client


def test_get_sync_client_concurrent_first_use_builds_once():
    seen = []
    barrier = threading.Barrier(8)

    def _worker():
        barrier.wait()  # maximize the chance of a genuine race
        seen.append(http_client.get_sync_client())

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == 8
    # Every thread must observe the same single client instance.
    assert len({id(c) for c in seen}) == 1
