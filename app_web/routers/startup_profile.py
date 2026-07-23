"""Startup profile API — the Models tab's "Startup" card (issue #265).

Backs the declarative "what should be up at launch" control surface:
``config/startup_profile.json`` is owned by ``src.startup_profile``; this
router is a thin CRUD shell over it plus the eligible-item metadata the
card needs to render toggles (service labels + the local models this host
can actually spawn).

  * ``GET   /api/startup-profile`` → current profile + the full eligible-item
    list (services, local models).
  * ``PATCH /api/startup-profile`` → merge-and-persist a partial update
    (e.g. toggling one flag or one model id at a time), validating against
    the active host's launchable model ids.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app_web.admin_forward import forward_admin_request
from src import startup_profile as sp
from src.host_profile import get_host
from src.host_profile import resolve as resolve_host
from src.model_registry import local_models
from src.remote_proxy import remote_auth_token, remote_base_url_for_host

logger = logging.getLogger(__name__)
router = APIRouter()

# Fixed metadata for the non-model startup toggles — labels the admin SPA
# renders next to each switch. Peer wake/sync is *not* here: since #374 it is
# owned by the fleet reconcile loop (config/fleet_placement.json), not a
# per-service startup flag — the old "Mac Mini sync" toggle was retired.
_SERVICE_ITEMS = [
    {"id": "docker", "label": "Docker"},
    {"id": "langfuse", "label": "Langfuse"},
    {"id": "agentsview", "label": "AgentsView"},
]

_SPAWNABLE_BACKENDS = ("openai", "whisper", "tts")


def _eligible_models() -> List[Dict[str, Any]]:
    return [
        {"id": m.id, "display_name": m.display_name}
        for m in local_models()
        if m.backend in _SPAWNABLE_BACKENDS and not m.virtual
    ]


def _remote_target(host: Optional[str]) -> Optional[str]:
    """Resolve ``host`` to a peer hub base URL to forward to, or ``None`` when
    the request targets this (active) host and should be served locally (#352).

    ``host`` absent or naming the active host → ``None`` (local). A known remote
    host with an ``address`` → its hub base URL. An unknown host → 404; a known
    host with no ``address`` (can't be reached) → 400.
    """
    if not host or host == resolve_host().id:
        return None
    if get_host(host) is None:
        raise HTTPException(status_code=404, detail=f"unknown host {host!r}")
    remote = remote_base_url_for_host(host)
    if remote is None:
        raise HTTPException(
            status_code=400, detail=f"host {host!r} has no address configured"
        )
    return remote


def _host_headers(host: str) -> Dict[str, str]:
    token = remote_auth_token(host)
    return {"Authorization": f"Bearer {token}"} if token else {}


@router.get("/api/startup-profile")
async def get_startup_profile(host: Optional[str] = Query(None)) -> Dict[str, Any]:
    """The addressed host's startup profile + eligible items.

    ``?host=<id>`` targets a peer hub's profile (forwarded, #352); omitted or
    self → this host's own profile and locally-launchable models, unchanged.
    """
    remote = _remote_target(host)
    if remote is not None:
        return await forward_admin_request(
            remote,
            "/admin/api/startup-profile",
            method="GET",
            headers=_host_headers(host),
            unreachable_detail=f"host {host!r} unreachable",
        )
    profile = sp.load_startup_profile()
    return {
        "profile": profile.as_dict(),
        "services": _SERVICE_ITEMS,
        "models": _eligible_models(),
    }


@router.patch("/api/startup-profile")
async def patch_startup_profile(
    payload: Dict[str, Any], host: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Merge ``payload`` over the addressed host's profile, validate, and persist.

    Accepts a partial body — e.g. ``{"docker": false}`` or ``{"models": [...]}``
    — so a single toggle click never has to resend the whole profile. ``?host=``
    forwards the write to a peer hub (#352); omitted or self writes locally.
    """
    remote = _remote_target(host)
    if remote is not None:
        return await forward_admin_request(
            remote,
            "/admin/api/startup-profile",
            method="PATCH",
            headers=_host_headers(host),
            json=payload,
            unreachable_detail=f"host {host!r} unreachable",
        )
    current = sp.load_startup_profile().as_dict()
    merged = {**current, **(payload or {})}
    try:
        saved = sp.save_startup_profile(merged)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "profile": saved.as_dict()}
