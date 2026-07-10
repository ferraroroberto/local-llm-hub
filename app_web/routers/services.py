"""Services tab API — Docker engine + Langfuse stack health & launch.

Surfaces the host-side ``src.services`` helpers behind two endpoints:

  * ``GET  /admin/api/services/status`` — quick combined probe used by
    the Hub tab's services card; polls every few seconds while the Hub
    tab is active.
  * ``POST /admin/api/services/launch`` — start Docker Desktop (if
    down) then run ``start_langfuse.bat`` / ``.sh``. Returns a final
    step log when the chain settles — the request can take ~30-90 s on
    a cold start. Idempotent: a no-op when both services are already up.

Both endpoints are loopback-bypass-safe (the bearer-token middleware
exempts loopback) so the SPA can call them without an admin token on
``127.0.0.1``.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict

from fastapi import APIRouter

from src import services as svc
from src.host_profile import MAC_MINI_HOST_ID, resolve as resolve_host

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/services/status")
async def services_status() -> Dict[str, Any]:
    """Combined Docker + Langfuse + Mac Mini probe.

    All probes have a short timeout so the worst case is a few seconds
    when everything is down; in steady state each returns in <100 ms.
    """
    docker = await svc.docker_status()
    langfuse = await svc.langfuse_health()
    # Informational only (#179) — skip self-probing when this hub *is*
    # the Mac Mini; the indicator exists to tell the other host's story.
    active = resolve_host()
    mac_mini = (
        await svc.mac_mini_health(MAC_MINI_HOST_ID)
        if active.id != MAC_MINI_HOST_ID
        else None
    )

    # The "launch" button is only meaningful on platforms where we know
    # how to spawn Docker Desktop. Surface that to the SPA so the card
    # can render an explanatory line instead of a button on macOS/Linux.
    launchable = (
        sys.platform == "win32"
        and svc.find_docker_desktop() is not None
    )

    return {
        "docker": docker,
        "langfuse": langfuse,
        "mac_mini": mac_mini,
        "mac_mini_host_id": MAC_MINI_HOST_ID,
        "launchable": launchable,
        "platform": sys.platform,
    }


@router.post("/api/services/launch")
async def services_launch() -> Dict[str, Any]:
    """Start Docker Desktop + the Langfuse stack. Returns a step log."""
    logger.info("🚀 /admin/api/services/launch — orchestrating Docker + Langfuse")
    result = await svc.launch_stack()
    if result["ok"]:
        logger.info("✅ services launch completed: %s", result["steps"])
    else:
        logger.warning("⚠️ services launch failed: %s", result["steps"])
    return result
