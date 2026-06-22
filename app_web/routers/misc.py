"""Catch-all routes for /admin: index, healthz, static reports."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from src.static_versioning import rewrite_index_html

from ._helpers import PROJECT_ROOT, STATIC_DIR

router = APIRouter()

FRONTIER_RUNS_DIR = PROJECT_ROOT / "docs" / "frontier" / "runs"
FRONTIER_LATEST_FILE = FRONTIER_RUNS_DIR / "LATEST"


@lru_cache(maxsize=1)
def _icon_sprite() -> str:
    """The vendored Lucide `<svg>` sprite, injected inline into the served page.

    The sprite must ship **in-document** (iOS Safari does not resolve external
    `<use href="file.svg#id">` references). We keep a single source — the
    vendored ``_vendored/icons/icons-sprite.html`` partial — and inject it at
    serve time rather than duplicating ~180 lines into ``index.html``. Cached
    once per process; a hub restart re-reads it.
    """
    sprite_path = STATIC_DIR / "_vendored" / "icons" / "icons-sprite.html"
    try:
        text = sprite_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    # Strip the leading HTML comment (its prose contains literal `<svg>`
    # examples, so a naive find("<svg") would land mid-comment and leak a
    # broken fragment as visible text). Drop all comments, keep the element.
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


@router.get("/", include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="index.html missing")
    asset_hashes = getattr(request.app.state, "asset_hashes", {}) or {}
    body = index_path.read_text(encoding="utf-8")
    stamped = rewrite_index_html(body, asset_hashes)
    sprite = _icon_sprite()
    if sprite:
        # Inject the inline Lucide sprite immediately after <body> so every
        # `<use href="#i-NAME">` resolves (in-document, iOS-safe).
        stamped = stamped.replace("<body>", "<body>\n" + sprite, 1)
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


@router.get("/frontier", include_in_schema=False)
async def frontier_report() -> FileResponse:
    if not FRONTIER_LATEST_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail="No frontier report has been generated yet",
        )

    run_id = FRONTIER_LATEST_FILE.read_text(encoding="utf-8").strip()
    if not run_id:
        raise HTTPException(
            status_code=404,
            detail="No frontier report has been generated yet",
        )

    report_path = (FRONTIER_RUNS_DIR / run_id / "frontier.html").resolve()
    runs_root = FRONTIER_RUNS_DIR.resolve()
    if runs_root not in report_path.parents or not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Frontier report not found for run {run_id!r}",
        )

    return FileResponse(
        report_path,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            "Referrer-Policy": "no-referrer",
        },
    )
