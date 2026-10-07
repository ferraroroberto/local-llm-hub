"""Host-side Claude Code usage parser + the Cld tab's ``get_summary`` API.

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

This module owns Claude JSONL parsing and the cross-vendor ``get_summary``
orchestration API only (#451). The record shape and small parsing helpers
shared with the Codex/Copilot/AgentsView parsers live in ``usage_common.py``;
the per-vendor $/Mtok pricing tables in ``usage_pricing.py``; the chart
time-series/period-comparison bucketing in ``usage_charts.py``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from src.usage_charts import MAX_DAILY_DAYS, build_prev_totals, build_time_series
from src.usage_common import (
    FileStats,
    UsageRecord,
    encode_project_key,
    iter_jsonl,
    load_cached,
    model_display,
    parse_iso_ts,
    period_since,
    project_pretty,
    today_utc,
)
from src.usage_pricing import record_costs

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CLAUDE_PROJECTS_DIR: Path = Path.home() / ".claude" / "projects"

# How many recent sessions to return in the summary.
_MAX_RECENT_SESSIONS = 15


# ---------------------------------------------------------------------------
# File-level mtime cache (module-level singleton)
# ---------------------------------------------------------------------------

_file_cache: Dict[str, FileStats] = {}


def _parse_jsonl_file(path: Path, project_key: str) -> List[UsageRecord]:
    """Parse one JSONL file and return usage records."""
    records: List[UsageRecord] = []
    try:
        for obj in iter_jsonl(path):

            if obj.get("type") != "assistant":
                continue

            msg = obj.get("message") or {}
            usage = msg.get("usage") or {}
            if not usage:
                continue

            # Timestamp — fall back gracefully.
            ts = parse_iso_ts(obj.get("timestamp", ""))

            model = msg.get("model") or "unknown"
            session_id = obj.get("sessionId") or str(path.stem)

            records.append(
                UsageRecord(
                    session_id=session_id,
                    project_key=project_key,
                    project_name=project_pretty(project_key),
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


def _load_file(path: Path, project_key: str) -> List[UsageRecord]:
    """Return cached records, re-parsing only when the file has changed."""
    return load_cached(path, _file_cache, lambda p: _parse_jsonl_file(p, project_key))


def _claude_records() -> List[UsageRecord]:
    """Scan all Claude Code project JSONL files and return every usage record."""
    records: List[UsageRecord] = []

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
            # Newer Claude Code also writes per-session directories holding
            # sub-agent transcripts (projects/<proj>/<session>/subagents/
            # agent-*.jsonl, same line format) — the Task-tool usage that
            # the flat files never carried (#68's blind spot, now partially
            # local). Lines carry the parent sessionId so they group with
            # their session where present.
            jsonl_files += list(proj_dir.glob("*/subagents/agent-*.jsonl"))
        except OSError:
            continue
        for jf in jsonl_files:
            records.extend(_load_file(jf, project_key))

    return records


_VALID_VENDORS = {"claude", "codex", "copilot", "all"}  # native set + "all"


def is_valid_vendor(vendor: str) -> bool:
    """Native vendors/"all" plus the curated AgentsView vendors (#280).

    ``KNOWN_VENDORS`` (e.g. ``agy``) stay valid even while AgentsView is
    down or absent — their history rollups remain queryable.  Snapshot-only
    lookups, no network, so this is safe on the router's validation path.
    """
    if vendor in _VALID_VENDORS:
        return True
    from src import agentsview_usage
    return (
        vendor in agentsview_usage.KNOWN_VENDORS
        or vendor in agentsview_usage.discovered_vendors()
    )


def _gather_records(vendor: str = "all") -> List[UsageRecord]:
    """Return usage records for the requested vendor(s).

    ``vendor`` is ``claude | codex | copilot | all`` or an AgentsView-sourced
    agent slug (issue #280).  Vendor modules are imported lazily so they can
    import shared helpers from this module without a cycle.
    """
    records: List[UsageRecord] = []
    if vendor in ("all", "claude"):
        records.extend(_claude_records())
    if vendor in ("all", "codex"):
        from src import codex_usage
        records.extend(codex_usage.all_records())
    if vendor in ("all", "copilot"):
        from src import copilot_usage
        records.extend(copilot_usage.all_records())
    if vendor == "all" or vendor not in _VALID_VENDORS:
        # AgentsView gap-fill vendors (never claude/codex/copilot — the
        # client excludes natives at the source, so "all" can't double-count).
        from src import agentsview_usage
        av = agentsview_usage.all_records()
        if vendor != "all":
            av = [r for r in av if r.vendor == vendor]
        records.extend(av)
    return records


# ---------------------------------------------------------------------------
# Public API — aggregation helpers
# ---------------------------------------------------------------------------


def _tok_k(n: int) -> float:
    """Round to one decimal in thousands."""
    return round(n / 1000, 1)


_VALID_PERIODS = {"today", "week", "month", "all"}


_OTEL_UNTRACKED_KEY = "otel-untracked"
_OTEL_FIELD_MAP = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_tokens",
    "cache_creation": "cache_creation_tokens",
}


def _otel_delta_records(records: List[UsageRecord]) -> List[UsageRecord]:
    """Claude usage the transcript sources never saw (#280 follow-up).

    Sessions bridged through claude.ai/code export OTel metrics (#68) but
    write no usage records to the local JSONL transcripts, so the OTel store
    is a superset of the transcript view whenever telemetry is enabled.  Per
    ``(day, model family)``, any per-field OTel excess over the claude
    records already gathered (live + history) becomes one synthetic record:
    ``vendor="claude"``, project ``(untracked)`` (OTel carries no project or
    session attribution), ``requests=0`` (no request count in the token
    metrics — token/cost tiles stay accurate, the requests tile only counts
    transcript-sourced calls).

    Recomputed from ``claude_code_otel``'s own persisted store on every call
    and **never folded into code_usage_history** — persisting a derived
    delta alongside its inputs would double-count when the inputs shift
    (e.g. partial pruning of a day).  Never raises.
    """
    try:
        from src import claude_code_otel
        otel_rows = claude_code_otel.get_usage_summary("all")["rows"]
    except Exception as exc:  # noqa: BLE001 — OTel store must never break the tab
        _log.warning("⚠️ code_usage: OTel delta unavailable: %s", exc)
        return []
    if not otel_rows:
        return []

    otel: Dict[Tuple[str, str], Dict[str, int]] = {}
    for row in otel_rows:
        key = (row["date"], model_display(str(row.get("model") or "unknown")))
        acc = otel.setdefault(key, {f: 0 for f in _OTEL_FIELD_MAP.values()})
        for src_field, dst_field in _OTEL_FIELD_MAP.items():
            acc[dst_field] += int(row.get(src_field) or 0)

    covered: Dict[Tuple[str, str], Dict[str, int]] = {}
    for r in records:
        if r.vendor != "claude":
            continue
        key = (r.ts.astimezone(timezone.utc).date().isoformat(), model_display(r.model))
        acc = covered.setdefault(key, {f: 0 for f in _OTEL_FIELD_MAP.values()})
        acc["input_tokens"] += r.input_tokens
        acc["output_tokens"] += r.output_tokens
        acc["cache_read_tokens"] += r.cache_read_tokens
        acc["cache_creation_tokens"] += r.cache_creation_tokens

    out: List[UsageRecord] = []
    for (day, family), sums in otel.items():
        have = covered.get((day, family), {})
        delta = {
            f: max(sums[f] - have.get(f, 0), 0)
            for f in _OTEL_FIELD_MAP.values()
        }
        if not any(delta.values()):
            continue
        d = date.fromisoformat(day)
        out.append(
            UsageRecord(
                session_id=f"otel:{day}",
                project_key=_OTEL_UNTRACKED_KEY,
                project_name="(untracked)",
                model=family,
                ts=datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc),
                input_tokens=delta["input_tokens"],
                output_tokens=delta["output_tokens"],
                cache_creation_tokens=delta["cache_creation_tokens"],
                cache_read_tokens=delta["cache_read_tokens"],
                vendor="claude",
                requests=0,
            )
        )
    return out


_COST_FIELDS = ("input_cost", "output_cost", "cache_read_cost")


def _blank_counts(with_cost: bool = False) -> dict:
    counts = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "reasoning_output_tokens": 0,
        "requests": 0,
    }
    if with_cost:
        counts.update({f: 0.0 for f in _COST_FIELDS})
    return counts


def _add_record(acc: dict, r: UsageRecord, with_cost: bool = False) -> None:
    """Fold one record's token counts (and, optionally, its equivalent API
    cost) into ``acc``."""
    acc["input_tokens"] += r.input_tokens
    acc["output_tokens"] += r.output_tokens
    acc["cache_creation_tokens"] += r.cache_creation_tokens
    acc["cache_read_tokens"] += r.cache_read_tokens
    acc["reasoning_output_tokens"] += r.reasoning_output_tokens
    acc["requests"] += r.requests
    if with_cost:
        for field, cost in zip(_COST_FIELDS, record_costs(r)):
            acc[field] += cost


def _group(records, key_fn, head_fn, *, with_cost: bool = False) -> dict:
    """Accumulate ``records`` into one row per ``key_fn(record)``.

    A row opens with ``head_fn(first record)`` (its identifying fields), then
    the token counts, then the cost fields when ``with_cost``. The one place a
    rollup change lands, so no breakdown can drift from the others.
    """
    groups: dict = {}
    for r in records:
        key = key_fn(r)
        row = groups.get(key)
        if row is None:
            row = groups[key] = {**head_fn(r), **_blank_counts(with_cost)}
        _add_record(row, r, with_cost)
    return groups


def _by_requests(groups: dict) -> list:
    return sorted(groups.values(), key=lambda x: x["requests"], reverse=True)


def get_summary(period: str = "today", vendor: str = "all") -> dict:
    """Return a summary dict consumed by the Cld tab.

    ``period`` is one of ``today | week | month | all``.
    ``vendor`` is ``claude | codex | copilot | all`` (issues #71, #231) or a
    dynamically-discovered AgentsView agent slug, e.g. ``gemini`` (#280).

    Keys returned:
      period     — echoed back
      vendor     — echoed back
      totals     — aggregate token counts for the requested period
      daily      — per-day list (last MAX_DAILY_DAYS days, newest first; all-time)
      by_model   — per-model-family breakdown for the requested period
      by_project — per-project breakdown for the requested period
      by_vendor  — per-vendor breakdown for the requested period
      recent_sessions — last _MAX_RECENT_SESSIONS sessions (all-time)
    """
    if period not in _VALID_PERIODS:
        period = "today"
    if not is_valid_vendor(vendor):
        vendor = "all"

    records = _gather_records(vendor)
    # Durable daily rollups (#280): fold what the parsers see right now into
    # the on-disk snapshot, then extend with synthetic records for days the
    # sources have already pruned (Claude Code deletes transcripts after
    # ~30 days) — "All" keeps growing instead of silently equalling "Month".
    from src import code_usage_history as _history
    _history.update_from_records(records)
    records = records + _history.synthetic_records(records, vendor)
    # Claude sessions bridged through claude.ai/code write no usage to the
    # local transcripts at all — the hub's OTel receiver (#68) is the only
    # channel that sees them. Top the Claude picture up with per-(day, model)
    # deltas vs everything above; recomputed each call from OTel's own
    # durable store, deliberately NOT persisted into the history snapshot.
    if vendor in ("all", "claude"):
        records = records + _otel_delta_records(records)
    today = today_utc()
    since = period_since(period)

    def in_period(r: UsageRecord) -> bool:
        return since is None or r.ts.astimezone(timezone.utc).date() >= since

    period_records = [r for r in records if in_period(r)]

    # ---- totals for the requested period (with equivalent API cost) ----
    totals = _blank_counts(with_cost=True)
    for r in period_records:
        _add_record(totals, r, with_cost=True)

    # ---- daily buckets (always last MAX_DAILY_DAYS calendar days) ----
    daily_map = _group(
        records,
        lambda r: r.ts.astimezone(timezone.utc).date(),
        lambda r: {"date": r.ts.astimezone(timezone.utc).date().isoformat()},
    )
    sorted_days = sorted(daily_map.keys(), reverse=True)
    daily_list = [daily_map[d] for d in sorted_days[:MAX_DAILY_DAYS]]

    # ---- per-model breakdown (period-scoped) ----
    by_model = _by_requests(_group(
        period_records,
        lambda r: model_display(r.model),
        lambda r: {"model": model_display(r.model)},
    ))

    # ---- per-project breakdown (period-scoped) ----
    by_project = _by_requests(_group(
        period_records,
        lambda r: r.project_key,
        lambda r: {"project_key": r.project_key, "project": r.project_name},
    ))

    # ---- per-vendor breakdown (period-scoped, with equivalent API cost) ----
    by_vendor = _by_requests(_group(
        period_records,
        lambda r: r.vendor,
        lambda r: {"vendor": r.vendor},
        with_cost=True,
    ))

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
                **_blank_counts(),
            }
        s = session_map[k]
        _add_record(s, r)
        if r.ts.isoformat() < s["first_ts"]:
            s["first_ts"] = r.ts.isoformat()
        if r.ts.isoformat() > s["last_ts"]:
            s["last_ts"] = r.ts.isoformat()
        s["model"] = r.model

    sessions_sorted = sorted(
        session_map.values(), key=lambda x: x["last_ts"], reverse=True
    )
    recent_sessions = sessions_sorted[:_MAX_RECENT_SESSIONS]

    time_series = build_time_series(records, period, today)
    prev_totals = build_prev_totals(records, period, today)

    result: dict = {
        "period": period,
        "vendor": vendor,
        "totals": totals,
        "daily": daily_list,
        "by_model": by_model,
        "by_project": by_project,
        "by_vendor": by_vendor,
        "recent_sessions": recent_sessions,
        "time_series": time_series,
    }
    if prev_totals is not None:
        result["prev_totals"] = prev_totals
    return result
