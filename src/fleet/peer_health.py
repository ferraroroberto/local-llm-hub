"""A peer hub's own health-watch verdict, for its row on the Machines tab.

Every hub samples only its own machine, so this machine's row reads the watch
in memory while a peer's row asks that peer's hub (``GET /admin/api/machines/
health``). The watch samples every ten minutes, so the answer is cached for a
minute rather than re-fetched on each 10 s Machines poll.

A peer whose verdict could not be read (an older build without the endpoint,
a timeout, a malformed body) is reported as ``unknown``, never as fine: a read
that failed establishes nothing about the machine.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Tuple

from src.fleet.remote_proxy import remote_auth_headers
from src.fleet.remote_stats import dial_address_async
from src.health.watch import card_fields
from src.host_profile import HostProfile, hub_port
from src.http_client import get_async_client

logger = logging.getLogger(__name__)

# The watch itself only moves every ~10 minutes: a minute of cache keeps the
# peer's hub out of the 10 s poll while a new verdict still shows promptly.
_TTL_READ_S = 60.0
# A failed read is retried sooner, so a peer that just came back is not held at
# "not confirmed" for a full minute.
_TTL_FAILED_S = 15.0
_TIMEOUT_S = 4.0

_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def not_confirmed(reason: str) -> Dict[str, Any]:
    """The summary for a verdict that could not be read."""
    return {
        "verdict": "unknown", "reason": reason, "fix": None, "sampled_at": None,
        "enabled": True, "findings": [],
    }


async def _fetch(host: HostProfile) -> Tuple[Dict[str, Any], bool]:
    """One read of the peer's verdict: ``(summary, read_ok)``."""
    address = await dial_address_async(host)
    if not address:
        return not_confirmed("No route to that machine's hub"), False
    url = f"http://{address}:{hub_port()}/admin/api/machines/health"
    try:
        resp = await get_async_client().get(
            url, headers=remote_auth_headers(host.id), timeout=_TIMEOUT_S
        )
        if resp.status_code >= 400:
            logger.info("ℹ️ peer health %s: HTTP %s", host.id, resp.status_code)
            return not_confirmed("That machine's hub did not report a health verdict"), False
        return card_fields(resp.json()), True
    except Exception as exc:  # noqa: BLE001 — network, bad JSON or a body missing fields
        logger.info("ℹ️ peer health %s: read failed (%s)", host.id, type(exc).__name__)
        return not_confirmed("Could not read that machine's health verdict"), False


async def peer_health_summary(host: HostProfile) -> Dict[str, Any]:
    """The peer hub's verdict in the Machines card's shape, cached briefly."""
    now = time.monotonic()
    hit = _cache.get(host.id)
    if hit and hit[0] > now:
        return hit[1]
    summary, read_ok = await _fetch(host)
    _cache[host.id] = (now + (_TTL_READ_S if read_ok else _TTL_FAILED_S), summary)
    return summary


def reset_cache() -> None:
    """Forget every cached verdict (a test starts from a cold cache)."""
    _cache.clear()
