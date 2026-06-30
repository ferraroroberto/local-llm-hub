"""Shared, pooled ``httpx`` clients for the hub.

Constructing an ``httpx.Client`` / ``httpx.AsyncClient`` is **expensive** —
each construction builds an SSL context and a fresh connection pool. On the
reference Windows box that measured ~0.26 s *per construction*, independent of
the request itself (a reused client answers a loopback call in ~1 ms). The hub
used to build a new client on every proxied / upstream call, so every request
paid that ~0.26 s tax on top of the actual work (issue #165; #163 fixed the
TTS-speech path first).

These module-level singletons are built once and reused for the lifetime of
the process, so each call pays ~1 ms instead. Both are created lazily — the
async one binds whatever event loop is running on first use — and closed on hub
shutdown via :func:`aclose` / :func:`close`.

Per-call timeouts differ widely (a 2 s health probe vs a 900 s image
generation), so the clients carry a single generous default and callers pass
the real timeout via the request's ``timeout=`` argument (httpx applies it
per-request). Do **not** close the shared client inside a request handler.

Backends that run in their *own* process (the Orpheus engine and the
whisper-vanilla shim live outside the hub process) can't use these — they hold
their own persistent client in their own process.
"""

from __future__ import annotations

from typing import Optional

import httpx

# A keep-alive pool large enough for the hub's concurrent loopback fan-out to
# the local backends; expiry keeps idle sockets from lingering forever.
_LIMITS = httpx.Limits(max_keepalive_connections=32, keepalive_expiry=60.0)
# Generous default — real per-call timeouts are passed on each request.
_DEFAULT_TIMEOUT = 300.0

_async_client: Optional[httpx.AsyncClient] = None
_sync_client: Optional[httpx.Client] = None


def get_async_client() -> httpx.AsyncClient:
    """Return the shared async client, building it on first use.

    Race-free under asyncio: there is no ``await`` between the check and the
    assignment, so concurrent first-callers can't both construct one.
    """
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, limits=_LIMITS)
    return _async_client


def get_sync_client() -> httpx.Client:
    """Return the shared sync client (for upstream helpers run in a threadpool)."""
    global _sync_client
    if _sync_client is None or _sync_client.is_closed:
        _sync_client = httpx.Client(timeout=_DEFAULT_TIMEOUT, limits=_LIMITS)
    return _sync_client


async def aclose() -> None:
    """Close the shared async client (hub shutdown)."""
    global _async_client
    if _async_client is not None and not _async_client.is_closed:
        await _async_client.aclose()
    _async_client = None


def close() -> None:
    """Close the shared sync client (hub shutdown)."""
    global _sync_client
    if _sync_client is not None and not _sync_client.is_closed:
        _sync_client.close()
    _sync_client = None
