"""Per-day, per-agent token history, precomputed and served from a cache.

Backs ``GET /admin/api/code/usage/history``. A full rebuild walks every agent's
local logs (tens of seconds cold), so a read never does it: it answers at once
from the last computed result, reports that result's age, and a recalculation
runs in a background thread on demand. A failed recalculation keeps the last
result and flags it stale. A day an agent was never measured on is ``unknown``,
never zero; an agent that reports no token counts is ``not_measured``.

Sources (all read-only, shared with the Code tab's parsers): the live logs of
Claude, Codex, Copilot, Pi and the AgentsView agents, plus
``code_usage_history``'s durable rollups for days a vendor has already pruned.
Pi is deliberately not folded into those rollups (they would surface it in the
Code tab's "all" view); its pruned days are kept by this cache's own
carry-forward instead. The claude.ai/code bridged-session OTel source stays a
separate ``claude_bridged`` agent, as the Code tab keeps it apart.

Days are UTC calendar days, like every other Code-tab rollup.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.atomic_write import atomic_write_text, read_json
from src.usage.usage_common import UsageRecord, model_display, today_utc

_log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CACHE_PATH: Path = _PROJECT_ROOT / "data" / "code_token_history.json"
_CACHE_PATH: Path = _DEFAULT_CACHE_PATH
_SCHEMA_VERSION = 1

BRIDGED = "claude_bridged"
# Agents with no token counts at the source: reported as not measured, never 0.
NOT_MEASURED = ("grok",)
# Always listed, so a caller sees "unknown" for an agent with no data yet
# instead of the key being absent.
KNOWN_VENDORS = ("claude", "codex", "copilot", "pi", "agy", BRIDGED) + NOT_MEASURED

MAX_RANGE_DAYS = 400
DEFAULT_RANGE_DAYS = 30

_COUNT_FIELDS = (
    "requests",
    "input_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "output_tokens",
)

_wall = time.time  # test seam

_lock = threading.Lock()      # guards _state
_refresh_lock = threading.Lock()  # held for the whole of one recalculation
_state: Optional[dict] = None  # lazy-loaded
_spawn_enabled = True


def _empty_state() -> dict:
    return {
        "days": {},            # iso day -> vendor -> cell
        "first_days": {},      # vendor -> earliest iso day with data
        "computed_at": None,   # epoch of the last *successful* recalculation
        "last_attempt_at": None,
        "last_error": None,    # set when the latest attempt failed
    }


def _reset_for_tests(path: Optional[Path] = None) -> None:
    """Wipe state, point at ``path`` (``None`` restores the default) and stop
    reads from spawning a background recalculation."""
    global _CACHE_PATH, _state, _spawn_enabled
    with _lock:
        _CACHE_PATH = path if path is not None else _DEFAULT_CACHE_PATH
        _state = None
        _spawn_enabled = False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load_locked() -> dict:
    global _state
    if _state is not None:
        return _state
    _state = _empty_state()
    raw = read_json(_CACHE_PATH, "token history cache")
    if isinstance(raw, dict) and raw.get("version") == _SCHEMA_VERSION:
        for key in _state:
            if key in raw:
                _state[key] = raw[key]
    return _state


def _save_locked() -> None:
    payload = {"version": _SCHEMA_VERSION, **(_state or _empty_state())}
    try:
        atomic_write_text(_CACHE_PATH, json.dumps(payload) + "\n")
    except OSError as exc:
        _log.warning("⚠️ token_history: save failed: %s", exc)


# ---------------------------------------------------------------------------
# Recalculation
# ---------------------------------------------------------------------------


def _blank() -> dict:
    return {f: 0 for f in _COUNT_FIELDS}


def _net_input(r: UsageRecord) -> int:
    """Input excluding cache reads. Codex reports its cached tokens as a subset
    of ``input_tokens``; every other agent reports them separately."""
    if r.vendor == "codex":
        return max(r.input_tokens - r.cache_read_tokens, 0)
    return r.input_tokens


def _add(acc: dict, r: UsageRecord) -> None:
    acc["requests"] += r.requests
    acc["input_tokens"] += _net_input(r)
    acc["cache_creation_tokens"] += r.cache_creation_tokens
    acc["cache_read_tokens"] += r.cache_read_tokens
    acc["output_tokens"] += r.output_tokens


def _cell_total(cell: dict) -> int:
    return sum(cell.get(f, 0) for f in _COUNT_FIELDS)


def _build_days(records: List[UsageRecord]) -> Dict[str, Dict[str, dict]]:
    """Fold records into ``day -> vendor -> cell`` (with model / project rows)."""
    days: Dict[str, Dict[str, dict]] = {}
    models: Dict[Tuple[str, str, str], dict] = {}
    projects: Dict[Tuple[str, str, str], dict] = {}
    for r in records:
        day = r.ts.astimezone(timezone.utc).date().isoformat()
        cell = days.setdefault(day, {}).setdefault(
            r.vendor, {**_blank(), "models": [], "projects": []}
        )
        _add(cell, r)
        family = model_display(r.model)
        row = models.get((day, r.vendor, family))
        if row is None:
            row = models[(day, r.vendor, family)] = {"model": family, **_blank()}
            cell["models"].append(row)
        _add(row, r)
        prow = projects.get((day, r.vendor, r.project_key))
        if prow is None:
            prow = projects[(day, r.vendor, r.project_key)] = {
                "project_key": r.project_key, "project": r.project_name, **_blank(),
            }
            cell["projects"].append(prow)
        _add(prow, r)
    for by_vendor in days.values():
        for cell in by_vendor.values():
            cell["models"].sort(key=lambda x: x["requests"], reverse=True)
            cell["projects"].sort(key=lambda x: x["requests"], reverse=True)
    return days


def _compute_days() -> Dict[str, Dict[str, dict]]:
    """Gather every source and return ``day -> vendor -> cell``. May raise."""
    from src.usage import code_usage, code_usage_history, pi_usage

    live = code_usage._gather_records("all") + pi_usage.all_records()
    # Same opportunistic fold the Code tab does on every poll, so a day is
    # captured even when nobody opens the tab before its transcripts are pruned.
    code_usage_history.update_from_records([r for r in live if r.vendor != "pi"])
    records = live + code_usage_history.synthetic_records(live, "all")
    bridged = [
        replace(r, vendor=BRIDGED) for r in code_usage._otel_delta_records(records)
    ]
    return _build_days(records + bridged)


def _carry_forward(
    new: Dict[str, Dict[str, dict]], old: Dict[str, Dict[str, dict]]
) -> None:
    """Keep each day's high-water mark per agent, in place on ``new``.

    A day a source has since pruned drops out of (or shrinks in) a fresh parse;
    the larger earlier cell wins so history never shrinks. The bridged OTel
    source is derived from the others and skipped, as ``code_usage`` does.
    """
    for day, by_vendor in old.items():
        for vendor, cell in by_vendor.items():
            if vendor == BRIDGED or not isinstance(cell, dict):
                continue
            fresh = new.setdefault(day, {}).get(vendor)
            if fresh is None or _cell_total(cell) > _cell_total(fresh):
                new[day][vendor] = cell


def _first_days(days: Dict[str, Dict[str, dict]]) -> Dict[str, str]:
    first: Dict[str, str] = {}
    for day in sorted(days):
        for vendor in days[day]:
            first.setdefault(vendor, day)
    return first


def _refresh_body() -> bool:
    """Run one recalculation (single-flight). Returns True when it replaced the
    cache; a failure keeps the previous result and records why."""
    if not _refresh_lock.acquire(blocking=False):
        return False
    started = time.monotonic()
    try:
        with _lock:
            _load_locked()["last_attempt_at"] = _wall()
        try:
            days = _compute_days()
            with _lock:
                _carry_forward(days, _load_locked()["days"])
        except Exception as exc:  # noqa: BLE001 — keep the old result, flag it stale
            with _lock:
                _load_locked()["last_error"] = f"{type(exc).__name__}: {exc}"
                _save_locked()
            _log.warning(
                "⚠️ token_history: recalculation failed, keeping the last result: %s",
                exc, exc_info=True,
            )
            return False
        with _lock:
            st = _load_locked()
            st.update(
                days=days,
                first_days=_first_days(days),
                computed_at=_wall(),
                last_error=None,
            )
            _save_locked()
        _log.info(
            "ℹ️ token_history: recalculated %d days in %.1fs",
            len(days), time.monotonic() - started,
        )
        return True
    finally:
        _refresh_lock.release()


def request_refresh() -> bool:
    """Start a background recalculation unless one is running. Never blocks."""
    if _refresh_lock.locked() or not _spawn_enabled:
        return False
    threading.Thread(
        target=_refresh_body, daemon=True, name="token-history-refresh"
    ).start()
    return True


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


def parse_range(
    from_: Optional[str], to: Optional[str]
) -> Tuple[date, date]:
    """Resolve the query range (inclusive UTC days). Raises ``ValueError``."""
    try:
        end = date.fromisoformat(to) if to else today_utc()
        start = (
            date.fromisoformat(from_)
            if from_
            else end - timedelta(days=DEFAULT_RANGE_DAYS - 1)
        )
    except ValueError as exc:
        raise ValueError(f"dates must be YYYY-MM-DD: {exc}") from exc
    if start > end:
        raise ValueError("'from' is after 'to'")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"range is longer than {MAX_RANGE_DAYS} days")
    return start, end


def _cell_for(
    st: dict, day: date, vendor: str, computed_day: Optional[date]
) -> dict:
    """One (day, agent) answer: measured numbers, ``unknown`` or ``not_measured``."""
    if vendor in NOT_MEASURED:
        return {"state": "not_measured"}
    iso = day.isoformat()
    cell = st["days"].get(iso, {}).get(vendor)
    if cell is not None:
        return {"state": "measured", **cell, "tokens_in": _tokens_in(cell)}
    first = st["first_days"].get(vendor)
    in_window = (
        first is not None
        and computed_day is not None
        and first <= iso
        and day <= computed_day
    )
    if not in_window:
        return {"state": "unknown"}
    return {"state": "measured", **_blank(), "tokens_in": 0, "models": [], "projects": []}


def _tokens_in(cell: dict) -> int:
    return int(cell.get("input_tokens", 0)) + int(cell.get("cache_creation_tokens", 0))


def get_history(
    from_: Optional[str] = None, to: Optional[str] = None, refresh: bool = False
) -> dict:
    """The cached history for the range, immediately, whatever its age.

    ``refresh`` starts a background recalculation. A cache that was never
    computed also starts one — once; after a failed first attempt the caller
    must ask with ``refresh`` — and answers ``state: "empty"`` with every cell
    ``unknown``. Raises ``ValueError`` on a bad range.
    """
    start, end = parse_range(from_, to)
    with _lock:
        never_attempted = _load_locked()["last_attempt_at"] is None
    if refresh or never_attempted:
        request_refresh()

    with _lock:
        st = _load_locked()
        computed_at = st["computed_at"]
        last_error = st["last_error"]
        last_attempt_at = st["last_attempt_at"]
        computed_day = (
            datetime.fromtimestamp(computed_at, tz=timezone.utc).date()
            if computed_at is not None
            else None
        )
        vendors = sorted(set(KNOWN_VENDORS) | set(st["first_days"]))
        days_out: List[dict] = []
        d = start
        while d <= end:
            days_out.append({
                "date": d.isoformat(),
                "vendors": {
                    v: _cell_for(st, d, v, computed_day) for v in vendors
                },
            })
            d += timedelta(days=1)
        first_days = dict(st["first_days"])

    if computed_at is None:
        state = "empty"
    else:
        state = "stale" if last_error else "ok"
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "cache": {
            "state": state,
            "computed_at": (
                datetime.fromtimestamp(computed_at, tz=timezone.utc).isoformat()
                if computed_at is not None else None
            ),
            "age_seconds": (
                round(max(_wall() - computed_at, 0.0), 1)
                if computed_at is not None else None
            ),
            "stale": state == "stale",
            "refreshing": _refresh_lock.locked(),
            "last_error": last_error,
            "last_attempt_at": (
                datetime.fromtimestamp(last_attempt_at, tz=timezone.utc).isoformat()
                if last_attempt_at is not None else None
            ),
        },
        "vendors": [
            {
                "vendor": v,
                "state": "not_measured" if v in NOT_MEASURED else "measured",
                "first_day": first_days.get(v),
            }
            for v in vendors
        ],
        "days": days_out,
    }
