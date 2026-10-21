"""Build-identity endpoint — `git_sha`, `built_at`, `asset_hash`.

Used by the SPA to surface "Build: <sha> · <time>" in the footer. Useful
when a phone-side user wonders whether they're on a stale cache or the
latest deploy.
"""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Request

from src.config_write import config_sha
from src.process_identity import IDENTITY
from src.static_versioning import asset_hash_for

router = APIRouter()


@router.get("/api/version")
async def version(request: Request) -> Dict[str, str]:
    asset_hashes = getattr(request.app.state, "asset_hashes", {}) or {}
    return {
        "git_sha": IDENTITY["git_sha"],
        "built_at": IDENTITY["built_at"],
        "asset_hash": asset_hash_for(asset_hashes, "styles.css") or "",
        # models.yaml HEAD sha (#424) — read live (unlike git_sha's
        # process-identity memoization): the config converges by git pull
        # without a restart, and the drift loop compares this across hubs.
        "config_sha": config_sha(),
    }
