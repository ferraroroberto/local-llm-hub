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

from src.code_usage import _VALID_PERIODS, get_summary

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/usage/summary")
async def code_usage_summary(
    period: str = Query("today", description="today | week | month | all"),
) -> dict:
    """Return totals, per-model and per-project breakdowns, and recent
    sessions for the requested period.  Safe to call frequently — the
    underlying parser caches by file mtime so unchanged files are not re-read.
    """
    if period not in _VALID_PERIODS:
        period = "today"
    try:
        return get_summary(period)
    except Exception as exc:
        logger.warning("⚠️ code_usage_summary error: %s", exc, exc_info=True)
        return {
            "period": period,
            "totals": {},
            "daily": [],
            "by_model": [],
            "by_project": [],
            "recent_sessions": [],
            "error": str(exc),
        }
