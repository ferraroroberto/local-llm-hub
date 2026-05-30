"""Host-side Claude Code usage parser.

Reads the JSONL session logs that Claude Code writes under
``~/.claude/projects/<encoded-path>/*.jsonl`` and aggregates them into
summaries suitable for the admin SPA's ``Cld`` tab.

Design constraints (from issue #20):
- **Read-only** — we never modify the JSONL files.
- **Zero subprocesses** — we parse the files ourselves rather than
  shelling out to ``bunx ccusage``.
- **Passive** — nothing runs on the Claude Code request path; the SPA
  polls this module on its own 30 s interval.
- **Mtime cache** — each file is only re-parsed when its mtime changes,
  so repeated polls are cheap.

Token fields used from each ``assistant`` entry::

    message.usage.input_tokens                   # net new prompt tokens
    message.usage.output_tokens                  # generated tokens
    message.usage.cache_creation_input_tokens    # tokens written to cache
    message.usage.cache_read_input_tokens        # tokens served from cache

``total_in`` for display purposes = ``input_tokens + cache_creation_input_tokens``
(both are "charged" in the Pro billing model).  ``cache_read`` is kept
separately so the SPA can show it with a visual distinction.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CLAUDE_PROJECTS_DIR: Path = Path.home() / ".claude" / "projects"

# How many recent sessions to return in the summary.
_MAX_RECENT_SESSIONS = 15

# How many days of daily history to return.
_MAX_DAILY_DAYS = 14

# How many weeks / months of history to return for the trend charts.
_MAX_CHART_WEEKS = 12
_MAX_CHART_MONTHS = 12


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


@dataclass
class _FileStats:
    """Cached parse result for one JSONL file."""

    mtime: float
    entries: List["_UsageRecord"]


@dataclass
class _UsageRecord:
    """One aggregated usage record (one assistant API call)."""

    session_id: str
    project_key: str       # encoded dir name, e.g. "E--automation-local-llm-hub"
    project_name: str      # pretty-printed project name
    model: str
    ts: datetime
    input_tokens: int      # net new prompt tokens
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int


# ---------------------------------------------------------------------------
# File-level mtime cache (module-level singleton)
# ---------------------------------------------------------------------------

_file_cache: Dict[str, _FileStats] = {}


def _project_pretty(key: str) -> str:
    """Turn an encoded project key into a readable name.

    ``E--automation-local-llm-hub`` → ``local-llm-hub``
    ``C--Users-rober--some-path`` → ``some-path``
    """
    # Drop the drive-letter prefix (up to and including the first "--")
    parts = key.split("--", 1)
    tail = parts[-1] if len(parts) > 1 else key
    # The last hyphen-segment is the repo/dir name.
    segments = tail.rsplit("-", 1)
    # Heuristic: if the tail contains more than one `-` segment AND the last
    # two segments look like a repo name, just return the original tail
    # (better to over-include than to mangle names like "local-llm-hub").
    return tail


def _parse_jsonl_file(path: Path, project_key: str) -> List[_UsageRecord]:
    """Parse one JSONL file and return usage records."""
    records: List[_UsageRecord] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if obj.get("type") != "assistant":
                    continue

                msg = obj.get("message") or {}
                usage = msg.get("usage") or {}
                if not usage:
                    continue

                # Timestamp — fall back gracefully.
                ts_raw = obj.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_raw.rstrip("Z")).replace(
                        tzinfo=timezone.utc
                    )
                except (ValueError, AttributeError):
                    ts = datetime.now(tz=timezone.utc)

                model = msg.get("model") or "unknown"
                session_id = obj.get("sessionId") or str(path.stem)

                records.append(
                    _UsageRecord(
                        session_id=session_id,
                        project_key=project_key,
                        project_name=_project_pretty(project_key),
                        model=model,
                        ts=ts,
                        input_tokens=int(usage.get("input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0),
                        cache_creation_tokens=int(
                            usage.get("cache_creation_input_tokens") or 0
                        ),
                        cache_read_tokens=int(
                            usage.get("cache_read_input_tokens") or 0
                        ),
                    )
                )
    except OSError as exc:
        _log.warning("⚠️ code_usage: cannot read %s: %s", path, exc)
    return records


def _load_file(path: Path, project_key: str) -> List[_UsageRecord]:
    """Return cached records, re-parsing only when the file has changed."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []

    key = str(path)
    cached = _file_cache.get(key)
    if cached is not None and cached.mtime == mtime:
        return cached.entries

    entries = _parse_jsonl_file(path, project_key)
    _file_cache[key] = _FileStats(mtime=mtime, entries=entries)
    return entries


def _all_records() -> List[_UsageRecord]:
    """Scan all project JSONL files and return every usage record."""
    records: List[_UsageRecord] = []

    if not _CLAUDE_PROJECTS_DIR.exists():
        return records

    try:
        project_dirs = [p for p in _CLAUDE_PROJECTS_DIR.iterdir() if p.is_dir()]
    except OSError as exc:
        _log.warning("⚠️ code_usage: cannot list %s: %s", _CLAUDE_PROJECTS_DIR, exc)
        return records

    for proj_dir in project_dirs:
        project_key = proj_dir.name
        try:
            jsonl_files = list(proj_dir.glob("*.jsonl"))
        except OSError:
            continue
        for jf in jsonl_files:
            records.extend(_load_file(jf, project_key))

    return records


# ---------------------------------------------------------------------------
# Public API — aggregation helpers
# ---------------------------------------------------------------------------


def _today_utc() -> date:
    return datetime.now(tz=timezone.utc).date()


def _tok_k(n: int) -> float:
    """Round to one decimal in thousands."""
    return round(n / 1000, 1)


def _model_display(model: str) -> str:
    """Shorten model IDs to a human-readable label."""
    m = model.lower()
    if "opus" in m:
        return "Opus"
    if "sonnet" in m:
        return "Sonnet"
    if "haiku" in m:
        return "Haiku"
    return model


def _week_start(d: date) -> date:
    """Return the Monday of the ISO week containing d."""
    from datetime import timedelta
    return d - timedelta(days=d.weekday())


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _build_time_series(
    records: List[_UsageRecord], period: str, today: date
) -> list:
    """Build oldest-first time-series buckets with per-model breakdown for the chart.

    Returned list is empty for ``period == "all"`` (unbounded x-axis is not useful).
    Each bucket: ``{"label": str, "models": {family: {input_tokens, output_tokens, requests}}}``.
    ``input_tokens`` already folds in ``cache_creation_tokens`` so the chart shows billed in.
    """
    from datetime import timedelta

    if period == "all":
        return []

    if period == "today":
        buckets = [today - timedelta(days=i) for i in range(_MAX_DAILY_DAYS - 1, -1, -1)]
    elif period == "week":
        this_mon = _week_start(today)
        buckets = [this_mon - timedelta(weeks=i) for i in range(_MAX_CHART_WEEKS - 1, -1, -1)]
    else:  # month
        ym: List[tuple] = []
        y, m = today.year, today.month
        for _ in range(_MAX_CHART_MONTHS):
            ym.append((y, m))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        buckets = [date(yr, mo, 1) for yr, mo in reversed(ym)]

    bucket_set = set(buckets)
    bmap: Dict[date, Dict[str, dict]] = {b: {} for b in buckets}

    for r in records:
        rd = r.ts.astimezone(timezone.utc).date()
        if period == "today":
            bk = rd
        elif period == "week":
            bk = _week_start(rd)
        else:
            bk = _month_start(rd)
        if bk not in bucket_set:
            continue
        family = _model_display(r.model)
        if family not in bmap[bk]:
            bmap[bk][family] = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "requests": 0}
        slot = bmap[bk][family]
        slot["input_tokens"] += r.input_tokens + r.cache_creation_tokens
        slot["output_tokens"] += r.output_tokens
        slot["cache_read_tokens"] += r.cache_read_tokens
        slot["requests"] += 1

    result = []
    for b in buckets:
        lbl = b.strftime("%b %Y") if period == "month" else b.strftime("%b ") + str(b.day)
        result.append({"label": lbl, "models": bmap[b]})
    return result


def _build_prev_totals(
    records: List[_UsageRecord], period: str, today: date
) -> Optional[dict]:
    """Return aggregate counts for the period immediately preceding the current window.

    today  → yesterday
    week   → 7 days ending last Sunday (today−13 .. today−7)
    month  → 30-day window ending 30 days ago (today−59 .. today−30)
    all    → None (omitted from response)
    """
    from datetime import timedelta

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
    found = False
    for r in records:
        d = r.ts.astimezone(timezone.utc).date()
        if lo <= d <= hi:
            acc["input_tokens"] += r.input_tokens
            acc["output_tokens"] += r.output_tokens
            acc["cache_creation_tokens"] += r.cache_creation_tokens
            acc["cache_read_tokens"] += r.cache_read_tokens
            acc["requests"] += 1
            found = True
    return acc if found else None


_VALID_PERIODS = {"today", "week", "month", "all"}


def _period_since(period: str) -> Optional[date]:
    """Return the earliest date (UTC) that belongs to ``period``, or None for all-time."""
    from datetime import timedelta
    today = _today_utc()
    if period == "today":
        return today
    if period == "week":
        return today - timedelta(days=6)
    if period == "month":
        return today - timedelta(days=29)
    # "all"
    return None


def get_summary(period: str = "today") -> dict:
    """Return a summary dict consumed by the Cld tab.

    ``period`` is one of ``today | week | month | all``.

    Keys returned:
      period     — echoed back
      totals     — aggregate token counts for the requested period
      daily      — per-day list (last _MAX_DAILY_DAYS days, newest first; all-time)
      by_model   — per-model-family breakdown for the requested period
      by_project — per-project breakdown for the requested period
      recent_sessions — last _MAX_RECENT_SESSIONS sessions (all-time)
    """
    if period not in _VALID_PERIODS:
        period = "today"

    records = _all_records()
    today = _today_utc()
    since = _period_since(period)

    # ---- helpers ----
    def blank_counts() -> dict:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "requests": 0,
        }

    def add_record(acc: dict, r: _UsageRecord) -> None:
        acc["input_tokens"] += r.input_tokens
        acc["output_tokens"] += r.output_tokens
        acc["cache_creation_tokens"] += r.cache_creation_tokens
        acc["cache_read_tokens"] += r.cache_read_tokens
        acc["requests"] += 1

    def in_period(r: _UsageRecord) -> bool:
        return since is None or r.ts.astimezone(timezone.utc).date() >= since

    # ---- totals for the requested period ----
    totals = blank_counts()
    for r in records:
        if in_period(r):
            add_record(totals, r)

    # ---- daily buckets (always last _MAX_DAILY_DAYS calendar days) ----
    daily_map: Dict[date, dict] = {}
    for r in records:
        d = r.ts.astimezone(timezone.utc).date()
        if d not in daily_map:
            daily_map[d] = {"date": d.isoformat(), **blank_counts()}
        add_record(daily_map[d], r)

    sorted_days = sorted(daily_map.keys(), reverse=True)
    daily_list = [daily_map[d] for d in sorted_days[:_MAX_DAILY_DAYS]]

    # ---- per-model breakdown (period-scoped) ----
    model_map: Dict[str, dict] = {}
    for r in records:
        if not in_period(r):
            continue
        label = _model_display(r.model)
        if label not in model_map:
            model_map[label] = {"model": label, **blank_counts()}
        add_record(model_map[label], r)
    by_model = sorted(
        model_map.values(), key=lambda x: x["requests"], reverse=True
    )

    # ---- per-project breakdown (period-scoped) ----
    proj_map: Dict[str, dict] = {}
    for r in records:
        if not in_period(r):
            continue
        key = r.project_key
        if key not in proj_map:
            proj_map[key] = {
                "project_key": key,
                "project": r.project_name,
                **blank_counts(),
            }
        add_record(proj_map[key], r)
    by_project = sorted(
        proj_map.values(), key=lambda x: x["requests"], reverse=True
    )

    # ---- recent sessions (always all-time, newest first) ----
    session_map: Dict[Tuple[str, str], dict] = {}
    for r in records:
        k = (r.project_key, r.session_id)
        if k not in session_map:
            session_map[k] = {
                "session_id": r.session_id,
                "project_key": r.project_key,
                "project": r.project_name,
                "model": r.model,
                "first_ts": r.ts.isoformat(),
                "last_ts": r.ts.isoformat(),
                **blank_counts(),
            }
        s = session_map[k]
        add_record(s, r)
        if r.ts.isoformat() < s["first_ts"]:
            s["first_ts"] = r.ts.isoformat()
        if r.ts.isoformat() > s["last_ts"]:
            s["last_ts"] = r.ts.isoformat()
        s["model"] = r.model

    sessions_sorted = sorted(
        session_map.values(), key=lambda x: x["last_ts"], reverse=True
    )
    recent_sessions = sessions_sorted[:_MAX_RECENT_SESSIONS]

    time_series = _build_time_series(records, period, today)
    prev_totals = _build_prev_totals(records, period, today)

    result: dict = {
        "period": period,
        "totals": totals,
        "daily": daily_list,
        "by_model": by_model,
        "by_project": by_project,
        "recent_sessions": recent_sessions,
        "time_series": time_series,
    }
    if prev_totals is not None:
        result["prev_totals"] = prev_totals
    return result


def get_today_totals_for_project(project_dir: str) -> Optional[dict]:
    """Quick helper for the status-line script (not used by the SPA router).

    ``project_dir`` is the raw filesystem path, e.g. ``E:\\automation\\local-llm-hub``.
    Returns a dict with ``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
    ``cache_creation_tokens``, ``requests`` for today, or ``None`` if no data.
    """
    # Encode the path the same way Claude Code does.
    encoded = (
        project_dir.replace(":\\", "--").replace("\\", "-").replace("/", "-")
    )
    proj_dir = _CLAUDE_PROJECTS_DIR / encoded
    if not proj_dir.is_dir():
        return None

    today = _today_utc()
    acc = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "requests": 0,
    }
    found = False
    for jf in proj_dir.glob("*.jsonl"):
        for r in _load_file(jf, encoded):
            if r.ts.astimezone(timezone.utc).date() == today:
                acc["input_tokens"] += r.input_tokens
                acc["output_tokens"] += r.output_tokens
                acc["cache_creation_tokens"] += r.cache_creation_tokens
                acc["cache_read_tokens"] += r.cache_read_tokens
                acc["requests"] += 1
                found = True

    return acc if found else None
