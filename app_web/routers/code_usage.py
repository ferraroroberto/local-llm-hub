"""Code-usage tab API — host-side Claude Code session data.

Exposes a single summary endpoint that the SPA's ``Cld`` tab polls
every 30 s while visible.  All data comes from parsing the JSONL logs
Claude Code writes to ``~/.claude/projects/<encoded>/*.jsonl`` — nothing
touches the Claude binary or the request path.

Mounts under ``/admin/api/code``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from src.code_usage import _VALID_PERIODS, get_summary, is_valid_vendor

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/usage/summary")
async def code_usage_summary(
    period: str = Query("today", description="today | week | month | all"),
    vendor: str = Query(
        "all", description="claude | codex | copilot | all | <agentsview agent>"
    ),
) -> dict:
    """Return totals, per-model / per-project / per-vendor breakdowns, and
    recent sessions for the requested period and vendor.  Safe to call
    frequently — the underlying parsers cache by file mtime so unchanged files
    are not re-read.  The ``agentsview`` block carries the optional external
    AgentsView service's reachability + discovered gap-fill vendors (#280) —
    the Code-tab mirror of the Telemetry tab's ``langfuse_reachable``.
    """
    from src import agentsview_usage

    if period not in _VALID_PERIODS:
        period = "today"
    if not is_valid_vendor(vendor):
        vendor = "all"
    try:
        body = get_summary(period, vendor)
        body["agentsview"] = agentsview_usage.status()
        return body
    except Exception as exc:
        logger.warning("⚠️ code_usage_summary error: %s", exc, exc_info=True)
        return {
            "period": period,
            "vendor": vendor,
            "totals": {},
            "daily": [],
            "by_model": [],
            "by_project": [],
            "by_vendor": [],
            "recent_sessions": [],
            "agentsview": {"enabled": False, "reachable": False, "vendors": []},
            "error": str(exc),
        }


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
