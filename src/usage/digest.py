"""Weekly coding-agent usage digest and per-agent budget alert, sent to Telegram.

Reads the cached per-day, per-agent token history (``token_history``) and
sends two kinds of message through the fleet transport (``notify_send``, via
``health.resource_alert``), so no Telegram client or recipient lives here:

* the **digest**, once a week at the configured local weekday and hour: each
  agent's last seven complete UTC days against the seven before;
* the **budget alert**, when an agent's tokens over the trailing seven UTC
  days (today included) pass the budget set for it, at most once per cooldown.

Tokens are net input + cache writes + output. Cache reads are left out: they
dominate every agent's count and cost a fraction of the rest, so a budget
set on them would never mean anything.

A history that was never computed, or an agent that reports no token counts,
is ``unknown`` / ``not measured`` in the message, never zero, and a digest is
not sent over a history that could not be read at all (it is retried).

Settings: ``config/usage_digest.json`` (re-read each tick, no restart).
State (what was sent, when): ``data/usage_digest_state.json``.

``python -m src.usage.digest`` prints the digest that would go out now and
sends nothing; ``--send`` delivers it.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.atomic_write import atomic_write_text, read_json
from src.health import resource_alert
from src.usage import token_history
from src.usage.usage_common import today_utc

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "usage_digest.json"
STATE_PATH = PROJECT_ROOT / "data" / "usage_digest_state.json"

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "interval_s": 900,
    "boot_delay_s": 120,
    "digest": {"enabled": True, "weekday": 0, "hour": 8, "category": "log"},
    "budget": {
        "tokens_per_week": {},
        "check_interval_hours": 6,
        "cooldown_hours": 24,
        "category": "attention",
    },
    "max_cache_age_s": 3600,
    "refresh_timeout_s": 600,
    "retry_after_s": 600,
    "script": None,
    "timeout_s": 30,
}

WINDOW_DAYS = 7


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """The file merged over :data:`DEFAULTS` one section deep."""
    merged = copy.deepcopy(DEFAULTS)
    data = read_json(path or CONFIG_PATH, "usage digest config")
    if not isinstance(data, dict):
        return merged
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update({k: v for k, v in value.items() if not k.startswith("_")})
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Pure report building
# ---------------------------------------------------------------------------


def _tokens(cell: dict) -> int:
    return (int(cell.get("input_tokens", 0)) + int(cell.get("cache_creation_tokens", 0))
            + int(cell.get("output_tokens", 0)))


def _sum_window(days: List[dict], vendor: str, start: date, end: date) -> Optional[Dict[str, int]]:
    """Tokens and requests of ``vendor`` over the inclusive days, ``None`` when no day was measured."""
    total = {"tokens": 0, "requests": 0}
    measured = False
    for day in days:
        if not start <= date.fromisoformat(day["date"]) <= end:
            continue
        cell = day["vendors"].get(vendor, {"state": "unknown"})
        if cell.get("state") != "measured":
            continue
        measured = True
        total["tokens"] += _tokens(cell)
        total["requests"] += int(cell.get("requests", 0))
    return total if measured else None


def build_report(history: dict, today: date) -> Dict[str, Any]:
    """Per-agent totals for the last two complete UTC weeks and the trailing week.

    ``today`` is a UTC day. The current week is the seven complete days ending
    yesterday, the previous one the seven before it; ``trailing`` is the seven
    days ending today (a partial last day), which the budget alert judges.
    """
    days = history["days"]
    cur_end = today - timedelta(days=1)
    cur_start = cur_end - timedelta(days=WINDOW_DAYS - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=WINDOW_DAYS - 1)
    trail_start = today - timedelta(days=WINDOW_DAYS - 1)
    agents: List[Dict[str, Any]] = []
    for entry in history["vendors"]:
        vendor = entry["vendor"]
        if entry["state"] == "not_measured":
            agents.append({"vendor": vendor, "state": "not_measured"})
            continue
        agents.append({
            "vendor": vendor, "state": "measured",
            "current": _sum_window(days, vendor, cur_start, cur_end),
            "previous": _sum_window(days, vendor, prev_start, prev_end),
            "trailing": _sum_window(days, vendor, trail_start, today),
        })
    return {"start": cur_start, "end": cur_end, "agents": agents}


def _compact(n: int) -> str:
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("k", 1_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def _change(current: int, previous: Optional[int]) -> str:
    if previous is None:
        return "no prior week"
    if previous == 0:
        return "new" if current else "no change"
    pct = round((current - previous) * 100 / previous)
    return f"{pct:+d}% vs prior week"


def _agent_line(agent: Dict[str, Any]) -> Optional[str]:
    vendor = agent["vendor"]
    if agent["state"] == "not_measured":
        return f"{vendor}: no token counts at the source"
    cur = agent["current"]
    if cur is None:
        return None if agent["previous"] is None else f"{vendor}: unknown this week"
    prev = agent["previous"]
    return (f"{vendor}: {_compact(cur['tokens'])} tokens, {cur['requests']:,} requests "
            f"({_change(cur['tokens'], None if prev is None else prev['tokens'])})")


def digest_text(report: Dict[str, Any], cache_age_s: Optional[float], stale: bool) -> str:
    """The digest message: one header, one line per agent that has anything to say."""
    head = f"Coding-agent usage, {report['start'].isoformat()} to {report['end'].isoformat()} (UTC)"
    lines = [head]
    lines += [line for line in map(_agent_line, report["agents"]) if line]
    measured = [a for a in report["agents"] if a["state"] == "measured" and a["current"]]
    if measured:
        lines.append("Total: " + _compact(sum(a["current"]["tokens"] for a in measured)) + " tokens")
    else:
        lines.append("No usage measured this week.")
    lines.append("Tokens = input + cache writes + output; cache reads left out.")
    if stale and cache_age_s is not None:
        lines.append(f"Warning: history could not be refreshed, figures are {cache_age_s / 3600:.0f} h old.")
    return "\n".join(lines)


def budget_breaches(report: Dict[str, Any], budgets: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Agents whose trailing-week tokens pass their budget. An unmeasured agent never breaches."""
    out: List[Dict[str, Any]] = []
    for agent in report["agents"]:
        limit = budgets.get(agent["vendor"])
        trailing = agent.get("trailing")
        if limit is None or agent["state"] != "measured" or trailing is None:
            continue
        if trailing["tokens"] > int(limit):
            out.append({"vendor": agent["vendor"], "tokens": trailing["tokens"], "limit": int(limit)})
    return out


def budget_text(breach: Dict[str, Any]) -> str:
    return (f"{breach['vendor']} used {_compact(breach['tokens'])} tokens over the last 7 days "
            f"(budget {_compact(breach['limit'])}).")


# ---------------------------------------------------------------------------
# Schedule and state
# ---------------------------------------------------------------------------


def last_scheduled(now: datetime, weekday: int, hour: int) -> datetime:
    """The most recent moment at or before ``now`` that is ``weekday`` at ``hour`` (local, tz-aware)."""
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    candidate -= timedelta(days=(candidate.weekday() - weekday) % 7)
    return candidate - timedelta(days=7) if candidate > now else candidate


def _load_state() -> Dict[str, Any]:
    raw = read_json(STATE_PATH, "usage digest state")
    state: Dict[str, Any] = {"digest_sent_at": None, "digest_attempt_at": None, "last_check_at": None, "alerts": {}}
    if isinstance(raw, dict):
        state.update({k: raw[k] for k in state if k in raw})
    return state


def _save_state(state: Dict[str, Any]) -> None:
    try:
        atomic_write_text(STATE_PATH, json.dumps(state, indent=2) + "\n")
    except OSError as exc:
        logger.warning("⚠️ usage digest: state not saved: %s", exc)


def _send(text: str, category: str, cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    # Looked up on the module at call time so the test suite's Telegram block applies.
    return resource_alert.send_via_notify_script(
        text, {"script": cfg["script"], "category": category, "timeout_s": cfg["timeout_s"]})


def _history(cfg: Dict[str, Any], today: date) -> Optional[Tuple[dict, bool]]:
    """``(history, stale)``, refreshing a cache older than ``max_cache_age_s`` first; ``None`` when unreadable."""
    start = (today - timedelta(days=2 * WINDOW_DAYS)).isoformat()
    hist = token_history.get_history(start, today.isoformat())
    age = hist["cache"]["age_seconds"]
    if age is None or age > float(cfg["max_cache_age_s"]):
        token_history.refresh_blocking(float(cfg["refresh_timeout_s"]))
        hist = token_history.get_history(start, today.isoformat())
    if hist["cache"]["state"] == "empty":
        return None
    return hist, bool(hist["cache"]["stale"])


# ---------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------


def _due_digest(cfg: Dict[str, Any], state: Dict[str, Any], now: datetime) -> bool:
    d = cfg["digest"]
    if not d["enabled"]:
        return False
    if state["digest_sent_at"] is None:
        return False  # seeded by tick(): a first start never sends a stale digest
    scheduled = last_scheduled(now, int(d["weekday"]), int(d["hour"])).timestamp()
    if state["digest_sent_at"] >= scheduled:
        return False
    attempt = state["digest_attempt_at"]
    return attempt is None or now.timestamp() - attempt >= float(cfg["retry_after_s"])


def _due_check(cfg: Dict[str, Any], state: Dict[str, Any], now_ts: float) -> bool:
    if not cfg["budget"]["tokens_per_week"]:
        return False
    last = state["last_check_at"]
    return last is None or now_ts - last >= float(cfg["budget"]["check_interval_hours"]) * 3600.0


def _run_alerts(cfg: Dict[str, Any], state: Dict[str, Any], report: Dict[str, Any], now_ts: float) -> List[str]:
    sent: List[str] = []
    b = cfg["budget"]
    for breach in budget_breaches(report, b["tokens_per_week"]):
        entry = state["alerts"].get(breach["vendor"], {})
        last_sent, last_try = entry.get("sent_at"), entry.get("attempt_at")
        if last_sent is not None and now_ts - last_sent < float(b["cooldown_hours"]) * 3600.0:
            continue
        if last_try is not None and now_ts - last_try < float(cfg["retry_after_s"]):
            continue
        ok, error = _send(budget_text(breach), b["category"], cfg)
        # A failed send does not start the cooldown: it is retried, never recorded as sent.
        state["alerts"][breach["vendor"]] = {"sent_at": now_ts if ok else last_sent, "attempt_at": now_ts}
        if ok:
            sent.append(breach["vendor"])
            logger.warning("🚨 usage digest: budget alert sent for %s", breach["vendor"])
        else:
            logger.warning("⚠️ usage digest: budget alert for %s NOT delivered (%s)", breach["vendor"], error)
    return sent


def tick(cfg: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Run whatever is due once; returns what happened. Blocking: run it in a thread."""
    cfg = cfg or load_config()
    now = now or datetime.now().astimezone()
    now_ts = now.timestamp()
    state = _load_state()
    result: Dict[str, Any] = {"digest": "not-due", "alerts": [], "history": "not-read"}
    if state["digest_sent_at"] is None:
        # The baseline is "now": the first run waits for the next scheduled moment.
        state["digest_sent_at"] = now_ts
        _save_state(state)
    want_digest, want_check = _due_digest(cfg, state, now), _due_check(cfg, state, now_ts)
    if not (want_digest or want_check):
        return result
    today = now.astimezone(timezone.utc).date()
    loaded = _history(cfg, today)
    if loaded is None:
        result["history"] = "unknown"
        logger.warning("⚠️ usage digest: token history not available yet, will retry")
        return result
    hist, stale = loaded
    result["history"] = "stale" if stale else "ok"
    report = build_report(hist, today)
    if want_digest:
        state["digest_attempt_at"] = now_ts
        ok, error = _send(digest_text(report, hist["cache"]["age_seconds"], stale), cfg["digest"]["category"], cfg)
        result["digest"] = "sent" if ok else "failed"
        if ok:
            state["digest_sent_at"] = now_ts
            logger.info("ℹ️ usage digest: weekly digest sent")
        else:
            logger.warning("⚠️ usage digest: weekly digest NOT delivered (%s)", error)
    if want_check:
        state["last_check_at"] = now_ts
        result["alerts"] = _run_alerts(cfg, state, report, now_ts)
    _save_state(state)
    return result


async def run_loop() -> None:
    """Tick forever on the configured cadence; never raises out of a tick."""
    cfg = await asyncio.to_thread(load_config)
    if not cfg["enabled"]:
        logger.info("ℹ️ usage digest: disabled in config/usage_digest.json")
        return
    await asyncio.sleep(float(cfg["boot_delay_s"]))
    while True:
        started = time.monotonic()
        try:
            cfg = await asyncio.to_thread(load_config)
            out = await asyncio.to_thread(tick, cfg)
            logger.debug("ℹ️ usage digest: %s", out)
        except Exception as exc:  # noqa: BLE001 — a failed tick must not end the loop
            logger.warning("⚠️ usage digest: tick failed: %s", exc, exc_info=True)
        await asyncio.sleep(max(60.0, float(cfg["interval_s"]) - (time.monotonic() - started)))


def main(argv: Optional[List[str]] = None) -> int:
    """Print the digest for now (nothing is sent); ``--send`` delivers it."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--send", action="store_true", help="deliver it to Telegram as well")
    args = parser.parse_args(argv)
    cfg = load_config()
    today = today_utc()
    loaded = _history(cfg, today)
    if loaded is None:
        print("token history not available", file=sys.stderr)
        return 1
    hist, stale = loaded
    text = digest_text(build_report(hist, today), hist["cache"]["age_seconds"], stale)
    print(text)
    if args.send:
        ok, error = _send(text, cfg["digest"]["category"], cfg)
        print("sent" if ok else f"NOT sent: {error}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
