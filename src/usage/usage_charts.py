"""Time-series bucketing and period-over-period comparison for the Cld tab.

Extracted out of ``code_usage.py`` (#451) — chart bucketing (weekly/monthly
rollups, the previous-period comparison) was one of five unrelated concerns
that module mixed together. ``code_usage.py`` imports :func:`build_time_series`
and :func:`build_prev_totals` for its ``get_summary`` aggregation; nothing
here depends back on it.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
from typing import Dict, List, Optional

from src.usage.usage_common import UsageRecord, model_display

# How many days of daily history to return (also bounds code_usage.get_summary's
# plain "daily" list, not just the "today"-period chart buckets here).
MAX_DAILY_DAYS = 14

# The trend chart's bar budget: a phone fits about a month of daily
# bars. Day/Week/Month stay under it; All draws weeks up to it, then months.
MAX_CHART_BARS = 31

# Daily bars per period. "today" keeps two weeks of context (a lone bar says
# nothing); "week" and "month" draw exactly their window (see period_since).
_DAILY_SPAN = {"today": MAX_DAILY_DAYS, "week": 7, "month": 30}


def _week_start(d: date) -> date:
    """Return the Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_starts(first: date, last: date) -> List[date]:
    out, d = [], _month_start(first)
    while d <= last:
        out.append(d)
        d = date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return out


def build_time_series(
    records: List[UsageRecord], period: str, today: date
) -> list:
    """Build oldest-first time-series buckets with per-model breakdown for the chart.

    Day, Week and Month are daily bars (14, 7 and 30 days). All spans the
    first record to today in weekly bars, or monthly once that would exceed
    ``MAX_CHART_BARS``; it is empty when there are no records at all.
    Each bucket: ``{"label": str, "models": {family: {input_tokens, output_tokens, requests}}}``.
    ``input_tokens`` already folds in ``cache_creation_tokens`` so the chart shows billed in.
    """
    def day_of(r: UsageRecord) -> date:
        return r.ts.astimezone(timezone.utc).date()

    if period == "all":
        if not records:
            return []
        first = min(day_of(r) for r in records)
        weeks = (_week_start(today) - _week_start(first)).days // 7 + 1
        if weeks <= MAX_CHART_BARS:
            unit = "week"
            buckets = [_week_start(first) + timedelta(weeks=i) for i in range(weeks)]
        else:
            unit = "month"
            buckets = _month_starts(first, today)
    else:
        unit = "day"
        span = _DAILY_SPAN.get(period, MAX_DAILY_DAYS)
        buckets = [today - timedelta(days=i) for i in range(span - 1, -1, -1)]

    to_bucket = {"day": lambda d: d, "week": _week_start, "month": _month_start}[unit]
    bmap: Dict[date, Dict[str, dict]] = {b: {} for b in buckets}

    for r in records:
        bk = to_bucket(day_of(r))
        if bk not in bmap:
            continue
        family = model_display(r.model)
        if family not in bmap[bk]:
            bmap[bk][family] = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "requests": 0}
        slot = bmap[bk][family]
        slot["input_tokens"] += r.input_tokens + r.cache_creation_tokens
        slot["output_tokens"] += r.output_tokens
        slot["cache_read_tokens"] += r.cache_read_tokens
        # ``requests`` is an aggregation weight, not a flag (see UsageRecord):
        # a code_usage_history synthetic rollup row carries the N calls it
        # summarises, and an OTel-derived record carries 0. Counting 1 per row
        # undercounted every pruned day and overcounted every OTel delta.
        slot["requests"] += r.requests

    result = []
    for b in buckets:
        lbl = b.strftime("%b %Y") if unit == "month" else b.strftime("%b ") + str(b.day)
        result.append({"label": lbl, "models": bmap[b]})
    return result


def build_prev_totals(
    records: List[UsageRecord], period: str, today: date
) -> Optional[dict]:
    """Return aggregate counts for the period immediately preceding the current window.

    today  → yesterday
    week   → 7 days ending last Sunday (today−13 .. today−7)
    month  → 30-day window ending 30 days ago (today−59 .. today−30)
    all    → None (omitted from response)

    For a non-"all" period the dict is always returned (zero-filled when the
    preceding window had no activity), so the SPA can show a "new" badge for a
    metric whose prior value was 0 instead of hiding the comparison entirely —
    e.g. a vendor like Codex that has no data in the previous week (issue #71).
    """
    if period == "all":
        return None

    if period == "today":
        lo = hi = today - timedelta(days=1)
    elif period == "week":
        lo, hi = today - timedelta(days=13), today - timedelta(days=7)
    else:  # month
        lo, hi = today - timedelta(days=59), today - timedelta(days=30)

    acc = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_tokens": 0, "cache_read_tokens": 0, "requests": 0,
    }
    for r in records:
        d = r.ts.astimezone(timezone.utc).date()
        if lo <= d <= hi:
            acc["input_tokens"] += r.input_tokens
            acc["output_tokens"] += r.output_tokens
            acc["cache_creation_tokens"] += r.cache_creation_tokens
            acc["cache_read_tokens"] += r.cache_read_tokens
            # Weight, not a flag — same reason as build_time_series.
            # The "month" window (days 30-59 back) is always entirely past
            # Claude Code's ~30-day transcript retention, so it is sourced
            # from synthetic rollup rows alone and undercounted by 1-per-row.
            acc["requests"] += r.requests
    return acc
