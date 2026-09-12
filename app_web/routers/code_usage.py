"""Code-usage tab API — host-side Claude Code session data.

Exposes a single summary endpoint that the SPA's ``Cld`` tab polls
every 30 s while visible.  All data comes from parsing the JSONL logs
Claude Code writes to ``~/.claude/projects/<encoded>/*.jsonl`` — nothing
touches the Claude binary or the request path.

Mounts under ``/admin/api/code``.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from src.code_usage import _VALID_PERIODS, get_summary, is_valid_vendor

logger = logging.getLogger(__name__)
router = APIRouter()


# ``response_model=None``: the annotation is a union with a Response subclass,
# which FastAPI cannot infer a response model from — the explicit opt-out keeps
# the hint (repo convention: type hints on every public function) without
# asking FastAPI to validate one branch of it.
@router.get("/usage/summary", response_model=None)
async def code_usage_summary(
    period: str = Query("today", description="today | week | month | all"),
    vendor: str = Query(
        "all", description="claude | codex | copilot | all | <agentsview agent>"
    ),
) -> dict | JSONResponse:
    """Return totals, per-model / per-project / per-vendor breakdowns, and
    recent sessions for the requested period and vendor.  Safe to call
    frequently — the underlying parsers cache by file mtime so unchanged files
    are not re-read.  The ``agentsview`` block carries the optional external
    AgentsView service's reachability + discovered gap-fill vendors (#280) —
    the Code-tab mirror of the Telemetry tab's ``langfuse_reachable``.

    A failure to build the summary is **not** a summary: it answers with HTTP
    503 rather than a 200 carrying empty totals (#580).  The old shape let a
    caller that never read the ``error`` field render "could not load" as a
    zero-usage day — the fleet's recurring defect, an unresolved lookup folded
    into the passing state.  A non-2xx makes that mistake impossible to make
    by omission: every JSON client already raises on it.
    """
    from src import agentsview_usage

    if period not in _VALID_PERIODS:
        period = "today"
    if not is_valid_vendor(vendor):
        vendor = "all"
    try:
        # Off the loop: a cold/changed-file parse would otherwise stall every
        # /v1/* request this process serves (#559).
        body = await asyncio.to_thread(get_summary, period, vendor)
        body["agentsview"] = agentsview_usage.status()
        return body
    except Exception as exc:
        logger.warning("⚠️ code_usage_summary error: %s", exc, exc_info=True)
        # No `totals`/`by_*` keys at all — an empty breakdown is a *value*, and
        # emitting one here is what let the tab draw a flat zero chart over a
        # failed load. `detail` is what api.js's jsonApi surfaces to the user.
        return JSONResponse(
            status_code=503,
            content={
                "period": period,
                "vendor": vendor,
                "error": str(exc),
                "detail": f"Could not build the code-usage summary: {exc}",
            },
        )


@router.get("/copilot/billing")
async def copilot_billing_summary() -> dict:
    """Return official per-day x per-model AI Credit spend from the GitHub
    billing API (issue #231, part B) — authoritative $ totals, no session or
    project attribution. Degrades to ``{"available": False, "reason": ...}``
    when no PAT is configured or the account isn't on the enhanced billing
    platform; never errors.
    """
    from src import copilot_billing

    try:
        return await copilot_billing.get_daily_credits()
    except Exception as exc:
        logger.warning("⚠️ copilot_billing_summary error: %s", exc, exc_info=True)
        return {
            "available": False,
            "reason": str(exc),
            "daily": [],
            "as_of": None,
        }
