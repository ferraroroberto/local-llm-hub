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
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from src import startup_profile as sp
from src.model_registry import local_models

logger = logging.getLogger(__name__)
router = APIRouter()

# Fixed metadata for the three non-model startup toggles — labels the
# admin SPA renders next to each switch.
_SERVICE_ITEMS = [
    {"id": "docker", "label": "Docker"},
    {"id": "langfuse", "label": "Langfuse"},
    {"id": "mac_mini_sync", "label": "Mac Mini sync"},
]

_SPAWNABLE_BACKENDS = ("openai", "whisper", "tts")


def _eligible_models() -> List[Dict[str, Any]]:
    return [
        {"id": m.id, "display_name": m.display_name}
        for m in local_models()
        if m.backend in _SPAWNABLE_BACKENDS and not m.virtual
    ]


@router.get("/api/startup-profile")
async def get_startup_profile() -> Dict[str, Any]:
    profile = sp.load_startup_profile()
    return {
        "profile": profile.as_dict(),
        "services": _SERVICE_ITEMS,
        "models": _eligible_models(),
    }


@router.patch("/api/startup-profile")
async def patch_startup_profile(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``payload`` over the current profile, validate, and persist.

    Accepts a partial body — e.g. ``{"docker": false}`` or
    ``{"models": [...]}`` — so a single toggle click never has to resend
    the whole profile.
    """
    current = sp.load_startup_profile().as_dict()
    merged = {**current, **(payload or {})}
    try:
        saved = sp.save_startup_profile(merged)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "profile": saved.as_dict()}
