"""Remote-host lifecycle API — bootstrap/sync a peer's dead hub (#181).

  * ``POST /admin/api/hosts/{host_id}/bootstrap`` — bring a *dead* remote
    host's hub back up over SSH (the LaunchAgent isn't guaranteed to have
    fired yet, or the process was killed by hand). Idempotent: a no-op
    (still returns ok) if the peer already answers.
  * ``POST /admin/api/hosts/{host_id}/sync`` — git-pull the remote host's
    checkout to this repo's latest and restart its hub.

Both require the normal bearer-token/allowlist auth (not exempt) since
they trigger real remote actions, unlike the read-only ``/api/version``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from src import remote_bootstrap

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/hosts/{host_id}/bootstrap")
async def host_bootstrap(host_id: str) -> Dict[str, Any]:
    logger.info("🛎️ /admin/api/hosts/%s/bootstrap", host_id)
    result = await remote_bootstrap.bootstrap_host(host_id)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["detail"])
    return result


@router.post("/api/hosts/{host_id}/sync")
async def host_sync(host_id: str) -> Dict[str, Any]:
    logger.info("🔃 /admin/api/hosts/%s/sync", host_id)
    result = await remote_bootstrap.sync_host(host_id)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result["detail"])
    return result
