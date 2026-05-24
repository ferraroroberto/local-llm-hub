"""Catch-all routes for /admin: index, healthz."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from src.static_versioning import rewrite_index_html

from ._helpers import STATIC_DIR

router = APIRouter()


@router.get("/", include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="index.html missing")
    asset_hashes = getattr(request.app.state, "asset_hashes", {}) or {}
    body = index_path.read_text(encoding="utf-8")
    stamped = rewrite_index_html(body, asset_hashes)
    # Force browsers (especially iOS Safari PWA) to revalidate the HTML
    # on every load so a stale cached index.html doesn't keep pointing
    # at a `?v=<old hash>` script that no longer exists after a deploy.
    return HTMLResponse(
        content=stamped,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.get("/api/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "service": "local-llm-hub-admin"}
