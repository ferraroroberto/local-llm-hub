"""Build-identity endpoint — `git_sha`, `built_at`, `asset_hash`.

Used by the SPA to surface "Build: <sha> · <time>" in the footer. Useful
when a phone-side user wonders whether they're on a stale cache or the
latest deploy.
"""

from __future__ import annotations

import datetime as _dt
import logging
import subprocess
from typing import Any, Dict

from fastapi import APIRouter, Request

from src.static_versioning import asset_hash_for

from ._helpers import PROJECT_ROOT

_log = logging.getLogger(__name__)
router = APIRouter()


def _resolve_git_sha() -> str:
    """Short git SHA, captured once at import. Falls back to ``"unknown"``."""
    cmd = ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short", "HEAD"]
    kwargs: Dict[str, Any] = dict(
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
    )
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(cmd, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning(
            "⚠️ /admin/api/version: git rev-parse raised %s: %s",
            type(exc).__name__,
            exc,
        )
        return "unknown"
    sha = (result.stdout or "").strip()
    return sha or "unknown"


_GIT_SHA = _resolve_git_sha()
_BUILT_AT = _dt.datetime.now().replace(microsecond=0).isoformat()


@router.get("/api/version")
async def version(request: Request) -> Dict[str, str]:
    asset_hashes = getattr(request.app.state, "asset_hashes", {}) or {}
    return {
        "git_sha": _GIT_SHA,
        "built_at": _BUILT_AT,
        "asset_hash": asset_hash_for(asset_hashes, "styles.css") or "",
    }
