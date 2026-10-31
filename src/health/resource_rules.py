"""Resource-watch judgements as pure functions of stored samples and config.

Three alert conditions, each a *sustained* breach (the last ``sustain_samples``
samples all past the limit) so one odd sample never pages anyone:

* RAM free below ``ram_free_percent.alert_below``.
* A drive's free space below ``disk_free_gb.alert_below``.
* A disk's average operation latency above ``disk_latency.alert_above_ms``,
  counted only from windows that completed at least ``min_ios`` operations in
  one direction: a near-idle disk with a single slow read is not a stall.

Each condition resolves to ``ok``, ``alert`` or ``unknown``. ``unknown`` is the
state of a reading that was not taken; it is never folded into ``ok``.
``unsupported`` (latency off Windows) is its own state and does not hold the
overall result at ``unknown``, the same rule the process health watch uses.

A *stall episode* is a run of consecutive samples past the latency limit at
least ``sustain_samples`` long. The weekly summary counts them per disk, and
says how much of the window was actually measured, so a quiet week with a
hub that was down half of it is never read as a clean one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Level ranking for the overall result: a known problem outranks a blind spot.
_RANK = {"ok": 0, "unknown": 1, "alert": 2}
# A gap wider than this many intervals between samples ends an episode: the hub
# was down or the probe failed, so the stall cannot be assumed to have continued.
_GAP_INTERVALS = 1.5


def stall_ms(row: Dict[str, Any], min_ios: int) -> Optional[float]:
    """The worst per-direction average latency of one drive row, or ``None``.

    ``None`` means there is nothing to judge: the latency reading is not ``ok``,
    or neither direction completed ``min_ios`` operations in the window."""
    if row.get("lat") != "ok":
        return None
    values = [
        row[f"{kind}_ms"] for kind in ("read", "write")
        if row.get(f"{kind}_ms") is not None and row.get(f"{kind}_ios", 0) >= min_ios
    ]
    return max(values) if values else None


def _breach(history: List[Dict[str, Any]], sustain: int, value_of, breached) -> str:
    """``alert`` / ``ok`` / ``unknown`` for one metric over the newest samples.

    ``value_of(sample)`` returns the metric or ``None`` (unreadable);
    ``breached(value)`` says whether it is past the limit."""
    if value_of(history[-1]) is None:
        return "unknown"
    tail = history[-sustain:]
    if len(tail) < sustain:
        return "ok"
    values = [value_of(s) for s in tail]
    return "alert" if all(v is not None and breached(v) for v in values) else "ok"


def _condition(key: str, kind: str, state: str, reason: str, drive: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    return {"key": key, "kind": kind, "drive": drive, "state": state, "reason": reason, **extra}


def _ram_condition(history: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    limit = float(cfg["ram_free_percent"]["alert_below"])
    sustain = int(cfg["ram_free_percent"]["sustain_samples"])
    state = _breach(history, sustain, lambda s: s["ram"].get("free_percent"), lambda v: v < limit)
    ram = history[-1]["ram"]
    if state == "unknown":
        return _condition("ram", "ram", state, "free RAM could not be read")
    reason = f"free RAM {ram['free_percent']}% ({ram['free_gb']} GB), limit {limit:g}%"
    if state == "alert":
        reason += f", below it for {sustain} samples"
    return _condition("ram", "ram", state, reason, value=ram["free_percent"])


def _drive_conditions(label: str, history: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    free_limit = float(cfg["disk_free_gb"]["alert_below"])
    free_sustain = int(cfg["disk_free_gb"]["sustain_samples"])

    def free_of(s):
        row = s["drives"].get(label)
        return row.get("free_gb") if row and row.get("free") == "ok" else None

    state = _breach(history, free_sustain, free_of, lambda v: v < free_limit)
    latest = history[-1]["drives"][label]
    reason = (f"{label} free space could not be read" if state == "unknown"
              else f"{label} has {latest['free_gb']} GB free, limit {free_limit:g} GB")
    out.append(_condition(f"disk_free:{label}", "disk_free", state, reason, label, value=latest.get("free_gb")))

    lat = cfg["disk_latency"]
    limit, sustain, min_ios = float(lat["alert_above_ms"]), int(lat["sustain_samples"]), int(lat["min_ios"])
    if latest.get("lat") == "unsupported":
        out.append(_condition(f"disk_latency:{label}", "disk_latency", "unsupported",
                              f"{label} latency is not measured on this platform", label))
        return out

    def stall_of(s):
        row = s["drives"].get(label)
        if row is None or row.get("lat") != "ok":
            return None
        # Too few operations to judge counts as a quiet 0, not as unreadable.
        return stall_ms(row, min_ios) or 0.0

    state = _breach(history, sustain, stall_of, lambda v: v > limit)
    now_ms = stall_of(history[-1])
    if state == "unknown":
        reason = f"{label} latency could not be read"
    else:
        reason = f"{label} average latency {now_ms:g} ms, limit {limit:g} ms"
        if state == "alert":
            reason += f", above it for {sustain} samples"
    out.append(_condition(f"disk_latency:{label}", "disk_latency", state, reason, label,
                          value=now_ms, disk=latest.get("disk")))
    return out


def evaluate(history: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every condition for the newest sample in ``history`` (oldest first)."""
    if not history:
        return []
    latest = history[-1]
    out = [_ram_condition(history, cfg)]
    if latest.get("drives_state") == "unknown":
        out.append(_condition("drives", "drives", "unknown", "the fixed-drive list could not be read"))
    for label in latest["drives"]:
        out.extend(_drive_conditions(label, history, cfg))
    return out


def overall(conditions: List[Dict[str, Any]]) -> str:
    """The worst state across ``conditions``; no conditions at all is ``unknown``."""
    ranked = [c["state"] for c in conditions if c["state"] in _RANK]
    return max(ranked, key=_RANK.__getitem__) if ranked else "unknown"


def stall_episodes(rows: List[Dict[str, Any]], cfg: Dict[str, Any], interval_s: float) -> List[Dict[str, Any]]:
    """Stall episodes in one drive's time-ordered ``[{ts, row}]`` series."""
    lat = cfg["disk_latency"]
    limit, need, min_ios = float(lat["alert_above_ms"]), int(lat["sustain_samples"]), int(lat["min_ios"])
    episodes: List[Dict[str, Any]] = []
    run: List[Dict[str, Any]] = []

    def close() -> None:
        if len(run) >= need:
            episodes.append({"start": run[0]["ts"], "end": run[-1]["ts"] + interval_s,
                             "minutes": round(len(run) * interval_s / 60.0, 1),
                             "worst_ms": max(r["ms"] for r in run)})
        run.clear()

    for entry in rows:
        ms = stall_ms(entry["row"], min_ios)
        contiguous = not run or entry["ts"] - run[-1]["ts"] <= _GAP_INTERVALS * interval_s
        if ms is not None and ms > limit and contiguous:
            run.append({"ts": entry["ts"], "ms": ms})
            continue
        close()
        if ms is not None and ms > limit:
            run.append({"ts": entry["ts"], "ms": ms})
    close()
    return episodes


def _coverage(series: List[Dict[str, Any]], now: float, window_s: float, interval_s: float) -> Dict[str, float]:
    """``{percent, span_s}``: the watched span (the window, or less on a young history) and the share of it measured."""
    measured = sum(1 for e in series if e["row"].get("lat") == "ok")
    span = min(window_s, now - series[0]["ts"] + interval_s) if series else 0.0
    percent = round(min(100.0, 100.0 * measured * interval_s / span), 1) if span > 0 else 0.0
    return {"percent": percent, "span_s": span}


def drive_summary(label: str, series: List[Dict[str, Any]], cfg: Dict[str, Any], *, now: float,
                  interval_s: float, window_days: float) -> Dict[str, Any]:
    """The per-drive stall summary for ``series`` (time-ordered ``[{ts, row}]``)."""
    window_s = window_days * 86400.0
    supported = bool(series) and series[-1]["row"].get("lat") != "unsupported"
    episodes = stall_episodes(series, cfg, interval_s) if supported else []
    cover = _coverage(series, now, window_s, interval_s) if supported else {"percent": 0.0, "span_s": 0.0}
    coverage = cover["percent"]
    # A young history says how long it has actually watched, not "7 days".
    watched = (f"the last {window_days:g} days" if cover["span_s"] >= window_s
               else f"{cover['span_s'] / 3600.0:.1f} h watched so far")
    total = round(sum(e["minutes"] for e in episodes), 1)
    worst = max((e["worst_ms"] for e in episodes), default=None)
    state = "ok"
    if not supported:
        state, line = "unsupported", f"{label} latency not measured on this platform"
    elif not episodes and coverage < float(cfg["min_coverage_percent"]):
        # An empty week is only "no stalls" if the disk was actually watched.
        state = "unknown"
        line = f"{label} not confirmed, latency measured for only {coverage:g}% of {watched}"
    elif episodes:
        line = (f"{label} {len(episodes)} stall{'s' if len(episodes) != 1 else ''}, {total:g} min total, "
                f"worst {worst:g} ms, {watched} ({coverage:g}% measured)")
    else:
        line = f"{label} no stalls in {watched} ({coverage:g}% measured)"
    return {"drive": label, "state": state, "episodes": len(episodes), "total_minutes": total,
            "worst_ms": worst, "covered_percent": coverage, "watched_hours": round(cover["span_s"] / 3600.0, 1),
            "line": line,
            "recent": [{"start": e["start"], "minutes": e["minutes"], "worst_ms": e["worst_ms"]}
                       for e in episodes[-5:]]}


def drive_series(samples: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Regroup oldest-first samples into ``{drive: [{ts, row}]}``."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for sample in samples:
        for label, row in sample["drives"].items():
            out.setdefault(label, []).append({"ts": sample["ts"], "row": row})
    return out
