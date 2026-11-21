"""Resource-watch judgements as pure functions of stored samples and config.

Four conditions, each a *sustained* breach (the last ``sustain_samples``
samples all past the limit) so one odd sample never pages anyone:

* RAM free below ``ram_free_percent.alert_below``.
* A drive's free space below ``disk_free_gb.alert_below``.
* A disk's average operation latency above ``disk_latency.alert_above_ms``,
  counted only from windows that completed at least ``min_ios`` operations in
  one direction: a near-idle disk with a single slow read is not a stall.
* A physical disk's *load*: busy, queued and slow all at once (``disk_load``).
  Busy alone is a big sequential copy and a deep queue alone is an NVMe doing
  its job; only the three together starve the apps waiting behind it. It has
  two levels, ``watch`` after a few minutes and ``alert`` after more, so the
  overload is visible before it pages.

Each condition resolves to ``ok``, ``alert`` or ``unknown`` (the load one also
``watch``). ``unknown`` is the state of a reading that was not taken; it is
never folded into ``ok``.
``unsupported`` (latency off Windows) is its own state and does not hold the
overall result at ``unknown``, the same rule the process health watch uses.

A *stall episode* is a run of consecutive samples past the latency limit at
least ``sustain_samples`` long. The weekly summary counts them per disk, and
says how much of the window was actually measured, so a quiet week with a
hub that was down half of it is never read as a clean one. A latency row
counts as measured only when it carries ``LATENCY_SOURCE``: a stored row from
any other source cannot be trusted, so it lowers coverage instead of reading
as a quiet disk.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Level ranking for the overall result: a known problem outranks a blind spot.
_RANK = {"ok": 0, "unknown": 1, "watch": 2, "alert": 3}
# A gap wider than this many intervals between samples ends an episode: the hub
# was down or the probe failed, so the stall cannot be assumed to have continued.
_GAP_INTERVALS = 1.5
# The latency source every trusted row is tagged with (the Windows performance
# counters). Untagged rows came from psutil's time fields, 1000 times too low.
LATENCY_SOURCE = "pdh"


def latency_ok(row: Dict[str, Any]) -> bool:
    """True when ``row`` holds a latency reading from the trusted source."""
    return row.get("lat") == "ok" and row.get("src") == LATENCY_SOURCE


def stall_ms(row: Dict[str, Any], min_ios: int) -> Optional[float]:
    """The worst per-direction average latency of one drive row, or ``None``.

    ``None`` means there is nothing to judge: the latency reading is not a
    trusted ``ok``, or neither direction completed ``min_ios`` operations in
    the window."""
    if not latency_ok(row):
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
        if row is None or not latency_ok(row):
            return None
        # Too few operations to judge counts as a quiet 0, not as unreadable.
        return stall_ms(row, min_ios) or 0.0

    state = _breach(history, sustain, stall_of, lambda v: v > limit)
    now_ms = stall_of(history[-1])
    if state == "unknown":
        reason = f"{label} latency could not be read"
        if latest.get("why"):
            reason += f" ({latest['why']})"
    else:
        reason = f"{label} average latency {now_ms:g} ms, limit {limit:g} ms"
        if state == "alert":
            reason += f", above it for {sustain} samples"
    out.append(_condition(f"disk_latency:{label}", "disk_latency", state, reason, label,
                          value=now_ms, disk=latest.get("disk")))
    return out


def worst_ms(row: Dict[str, Any]) -> Optional[float]:
    """The slower direction's average latency in a window, or ``None`` when no operation completed."""
    values = [row[f"{kind}_ms"] for kind in ("read", "write") if row.get(f"{kind}_ms") is not None]
    return max(values) if values else None


def load_level(row: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """``ok`` / ``watch`` / ``alert`` for one window of one disk, ``None`` when it cannot be judged.

    A level needs busy, queue and latency past its limits together. A window in
    which requests are queued on a busy disk yet none completed is the worst
    stall there is, so it counts as slow instead of as unmeasured."""
    if not latency_ok(row) or row.get("busy_percent") is None or row.get("queue") is None:
        return None
    ms = worst_ms(row)
    for level in ("alert", "watch"):
        slow = ms is None or ms >= float(cfg[f"latency_{level}_ms"])
        if row["busy_percent"] >= float(cfg[f"busy_{level}_percent"]) and row["queue"] >= float(cfg[f"queue_{level}"]) and slow:
            return level
    return "ok"


def _disks(sample: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``{disk: {labels, row}}``: the drive letters sharing each physical disk in one sample.

    Drives on one disk read the same counters, so they are one subject: one
    condition and one alert per disk, naming every letter on it."""
    out: Dict[str, Dict[str, Any]] = {}
    for label, row in sorted(sample["drives"].items()):
        if row.get("disk") is None or row.get("lat") == "unsupported":
            continue
        out.setdefault(row["disk"], {"labels": [], "row": row})["labels"].append(label)
    return out


def _load_describe(labels: List[str], row: Dict[str, Any]) -> str:
    ms = worst_ms(row)
    speed = f"{ms:g} ms per operation" if ms is not None else "no operation completed"
    return f"{', '.join(labels)} disk {row['busy_percent']:g}% busy, queue {row['queue']:g}, {speed}"


def load_condition(disk: str, labels: List[str], history: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The ``disk_load`` condition for one physical disk over the newest samples."""
    levels = []
    for sample in history[-int(cfg["alert_sustain_samples"]):]:
        entry = _disks(sample).get(disk)
        levels.append(load_level(entry["row"], cfg) if entry else None)
    row = _disks(history[-1])[disk]["row"]
    key = f"disk_load:{disk}"
    if levels[-1] is None:
        reason = f"{', '.join(labels)} disk load could not be read"
        return _condition(key, "disk_load", "unknown", reason, ", ".join(labels), disk=disk)
    state = "ok"
    watch_need, alert_need = int(cfg["watch_sustain_samples"]), int(cfg["alert_sustain_samples"])
    if len(levels) >= alert_need and all(v == "alert" for v in levels):
        state = "alert"
    elif len(levels) >= watch_need and all(v in ("watch", "alert") for v in levels[-watch_need:]):
        state = "watch"
    reason = _load_describe(labels, row)
    if state != "ok":
        reason += f", {'overloaded' if state == 'alert' else 'loaded'} for {alert_need if state == 'alert' else watch_need} samples"
    return _condition(key, "disk_load", state, reason, ", ".join(labels), disk=disk,
                      busy_percent=row["busy_percent"], queue=row["queue"], latency_ms=worst_ms(row))


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
    for disk, entry in _disks(latest).items():
        out.append(load_condition(disk, entry["labels"], history, cfg["disk_load"]))
    return out


_LOAD_FIX = "Stop or move the heavy disk work on this drive; Resource Monitor's Disk tab names the process"
_FINDING_LEVEL = {"alert": "act", "watch": "watch", "unknown": "unknown"}


def disk_finding(cond: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A ``disk_load`` condition as a machine-verdict finding (``alert`` -> ``act``), ``None`` when fine."""
    level = _FINDING_LEVEL.get(cond["state"])
    if level is None:
        return None
    return {"level": level, "kind": "disk", "metric": "disk_load", "value": cond.get("busy_percent"),
            "process": None, "reason": cond["reason"], "fix": _LOAD_FIX if level != "unknown" else None}


def disk_finding_unknown(reason: str) -> Dict[str, Any]:
    return {"level": "unknown", "kind": "disk", "metric": "disk_load", "value": None,
            "process": None, "reason": reason, "fix": None}


def disk_block(cond: Dict[str, Any], sample: Dict[str, Any]) -> Dict[str, Any]:
    """One disk's live load for the verdict payload: drive letters, busy %, queue, latency, state."""
    row = _disks(sample).get(cond["disk"], {}).get("row", {})
    return {"disk": cond["disk"], "drives": cond["drive"].split(", "), "state": cond["state"],
            "busy_percent": row.get("busy_percent"), "queue": row.get("queue"),
            "read_ms": row.get("read_ms"), "write_ms": row.get("write_ms")}


def worst_first(conditions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The conditions that are not fine (``watch``, ``alert``, ``unknown``), worst first."""
    firing = [c for c in conditions if _RANK.get(c["state"], 0) > 0]
    return sorted(firing, key=lambda c: _RANK[c["state"]], reverse=True)


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
    measured = sum(1 for e in series if latency_ok(e["row"]))
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
    if cover["span_s"] >= window_s:
        watched = f"the last {window_days:g} days"
    elif cover["span_s"] < 3600.0:
        watched = f"{cover['span_s'] / 60.0:.0f} min watched so far"
    else:
        watched = f"{cover['span_s'] / 3600.0:.1f} h watched so far"
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
