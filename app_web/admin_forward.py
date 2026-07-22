"""Forward an admin-API call to a peer hub (#178, #352).

The single transport that both the model-control forwards (``routers/models.py``)
and the host-addressed forwards (``routers/startup_profile.py``) share, so every
cross-host admin call mirrors a peer's response identically: a peer 4xx/409
surfaces verbatim, and an unreachable peer collapses to 502. Kept here in
``app_web`` (not ``src``) because it raises FastAPI ``HTTPException`` — the
non-web ``src`` layer must stay framework-free.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)


async def forward_admin_request(
    base_url: str,
    path: str,
    *,
    method: str,
    headers: Optional[Dict[str, str]] = None,
    unreachable_detail: str = "peer hub unreachable",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Forward ``method`` to ``{base_url}{path}`` and return the peer's JSON body.

    Raises 502 when the peer hub can't be reached, and mirrors any peer >=400
    (status + detail) verbatim so the caller sees the same error it would get
    hitting the peer directly. ``**kwargs`` pass through to ``httpx.request``
    (e.g. ``json=`` for a body, ``params=`` for a query).
    """
    url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.request(method, url, headers=headers or {}, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"{unreachable_detail}: {exc}")
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {"detail": r.text[:300]}
    if r.status_code >= 400:
        detail = body.get("detail", body) if isinstance(body, dict) else body
        raise HTTPException(status_code=r.status_code, detail=detail)
    return body
