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

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/services/status")
async def services_status() -> Dict[str, Any]:
    """Combined Docker + Langfuse probe.

    Both probes have a 2 s timeout so the worst case is ~4 s when both
    services are down; in steady state both return in <100 ms.
    """
    docker = await svc.docker_status()
    langfuse = await svc.langfuse_health()

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
