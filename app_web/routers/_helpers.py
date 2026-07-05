"""Cross-router helpers — no router imports another router; shared utility
lives here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, TypeVar

from fastapi import Request
from fastapi.responses import StreamingResponse

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

T = TypeVar("T")


async def maybe_json(request: Request) -> Dict[str, Any]:
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def sse_pack(data: Any, event: str = "") -> str:
    """Format one Server-Sent-Events frame. Shared by every /admin SSE
    endpoint so the wire format lives in exactly one place."""
    body = data if isinstance(data, str) else json.dumps(data)
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {body}\n\n"


def sse_stream(
    request: Request,
    subscribe: Callable[[], "asyncio.Queue[T]"],
    unsubscribe: Callable[["asyncio.Queue[T]"], None],
    seed: List[Any],
    to_dict: Optional[Callable[[T], Any]] = None,
    *,
    reverse_seed: bool = False,
    poll_timeout: float = 10.0,
) -> StreamingResponse:
    """Generic subscribe -> seed -> disconnect/keepalive -> unsubscribe SSE
    skeleton — shared by hub.py's ``log_tail``/``requests_stream`` and
    telemetry.py's ``telemetry_stream`` (issue #195), which were three
    near-identical copies of this generator (``telemetry_stream`` even
    reimplemented ``_sse_pack`` inline) before this helper existed.

    ``seed`` items are already in the shape ``sse_pack`` expects (the seed
    accessors — ``recent_requests()``, ``lines()`` — already return that
    shape) and are sent as-is. Only items arriving live off the queue are
    passed through ``to_dict`` first, matching each stream's original
    behavior (e.g. ``OBS``'s live queue carries raw ``RequestRecord``
    objects that need ``_rec_to_dict`` before packing; the seed doesn't).
    """
    q = subscribe()
    seed_items = list(reversed(seed)) if reverse_seed else list(seed)

    async def _gen() -> AsyncIterator[str]:
        try:
            for item in seed_items:
                yield sse_pack(item)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=poll_timeout)
                    yield sse_pack(to_dict(item) if to_dict else item)
                except asyncio.TimeoutError:
                    # Heartbeat keeps the connection (and any proxy) alive.
                    yield ":keepalive\n\n"
        finally:
            unsubscribe(q)

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })
