"""Catch-all routes for /admin: index, healthz, static reports."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from src.static_versioning import rewrite_index_html

from ._helpers import PROJECT_ROOT, STATIC_DIR

router = APIRouter()

FRONTIER_RUNS_DIR = PROJECT_ROOT / "docs" / "frontier" / "runs"
FRONTIER_LATEST_FILE = FRONTIER_RUNS_DIR / "LATEST"


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
