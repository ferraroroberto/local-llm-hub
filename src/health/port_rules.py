"""Port findings, offenders and the endpoint's ``ports`` block: pure functions of
the port history, the exhaustion events and the config.

A level is judged on the worst sample inside ``hold_minutes``, not only the
newest one: a burst that drains in two minutes must still read ``act`` for
whoever looks a few minutes later, and the reason says when it peaked. A
logged exhaustion event holds the verdict for ``event_hold_hours`` after it, even
when no sample caught the burst. 4231 and 4266 (the dynamic range ran out) are
``act``; a 4227 (a port still in TIME_WAIT could not be reused) is connection
churn, so it is ``watch`` unless the samples around it were at the ``act`` line.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .classifier import fmt_count, restart_fix
from .port_probe import SNAPSHOT_EVENTS
from .port_trace import TraceState

LABELS = {"tcp": "TCP", "udp": "UDP"}
RUNBOOK_FIX = ("No reboot: ports drain in minutes once the burst stops; find the client opening "
               "sockets in a burst (fleet-config's port-exhaustion runbook)")
_HOUR = 3600.0
# Growth per hour needs at least this much history to mean anything.
_MIN_GROWTH_SPAN_S = 300.0


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _level(value: float, rule: Dict[str, Any]) -> str:
    if rule.get("act") is not None and value >= rule["act"]:
        return "act"
    if rule.get("watch") is not None and value >= rule["watch"]:
        return "watch"
    return "ok"


def _peak(history: Sequence[Dict[str, Any]], read, since: float) -> Optional[Tuple[float, Dict[str, Any]]]:
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for sample in history:
        if sample["ts"] < since:
            continue
        value = read(sample)
        if value is not None and (best is None or value > best[0]):
            best = (float(value), sample)
    return best


def _percent(protocol: str):
    return lambda s: (s.get(protocol) or {}).get("percent")


def _time_wait(sample: Dict[str, Any]) -> Optional[int]:
    return (sample.get("tcp") or {}).get("time_wait")


def who(holder: Dict[str, Any]) -> str:
    """A holder's name, with its repo and entry point in brackets when the watch could read them."""
    label = holder.get("label")
    return f"{holder['name']} ({label})" if label else holder["name"]


def _top_holder(sample: Dict[str, Any], protocol: str) -> Optional[Dict[str, Any]]:
    holders = [h for h in sample.get("holders", []) if h.get(protocol)]
    return max(holders, key=lambda h: h[protocol]) if holders else None


def _pressure_finding(history, cfg, now, metric, read, label, unit, protocol) -> Optional[Dict[str, Any]]:
    """One threshold finding on the worst sample in the hold window."""
    peak = _peak(history, read, now - float(cfg["hold_minutes"]) * 60)
    if peak is None:
        return None
    value, at = peak
    level = _level(value, cfg[metric])
    if level == "ok":
        return None
    current = read(history[-1])
    reason = f"{label} {fmt_count(value)}{unit}"
    if at is not history[-1]:
        reason = f"{label} peaked at {fmt_count(value)}{unit} at {_clock(at['ts'])} (now {fmt_count(current or 0)}{unit})"
    fix, process = RUNBOOK_FIX, None
    if not protocol and at.get("time_wait_groups"):
        # A TIME_WAIT wave has no owning process; the port it hit and that port's listener name it.
        reason += ", " + groups_text(at["time_wait_groups"])
    holder = _top_holder(at, protocol) if protocol else None
    used = (at.get(protocol) or {}).get("used") if protocol else None
    if holder:
        process = holder["name"]
        reason += f", mostly {who(holder)} ({fmt_count(holder[protocol])} ports)"
        if used and holder[protocol] / used >= float(cfg.get("attribution_share", 0.5)):
            fix = restart_fix(process, cfg)
    return {"level": level, "kind": "system", "metric": metric, "value": value, "process": process,
            "ts": at["ts"], "reason": reason, "fix": fix}


_CHURN_EVENT = 4227


def _churn_at_act_line(history: Sequence[Dict[str, Any]], cfg: Dict[str, Any], ts: float) -> bool:
    """Whether TCP ports or TIME_WAIT reached their ``act`` line within ``hold_minutes`` of ``ts``."""
    span = float(cfg["hold_minutes"]) * 60
    near = [s for s in history if abs(s["ts"] - ts) <= span]
    for metric, read in (("tcp_percent", _percent("tcp")), ("tcp_time_wait", _time_wait)):
        peak = _peak(near, read, float("-inf"))
        if peak is not None and _level(peak[0], cfg[metric]) == "act":
            return True
    return False


def groups_text(groups: Sequence[Dict[str, Any]]) -> str:
    """The top three TIME_WAIT groups: the server port each socket points at, and its listener."""
    return "time wait mostly on " + ", ".join(
        f"port {g['port']} ({g['listener'] or 'not a local listener'}, {fmt_count(g['count'])} sockets)"
        for g in groups[:3])


def snapshot_text(event: Dict[str, Any]) -> str:
    """What the immediate snapshot of an exhaustion event found, with its delay after the event.
    A snapshot that held nothing, or could not read a table, is "not confirmed": a drained
    burst is not proof that nothing held the ports."""
    snap = event.get("snapshot")
    if not snap:
        return "no holder snapshot, so the holder is not confirmed"
    delay = f"{fmt_count(snap['delay_s'])} s after the event"
    parts = []
    groups = snap.get("time_wait_groups") or []
    if groups:
        parts.append(groups_text(groups))
    holders = snap.get("holders") or []
    protocol = event.get("protocol")
    if holders:
        # Ranked by the event's own protocol, so the count shown is that protocol's, not tcp + udp.
        label = f"{LABELS[protocol]} ports" if protocol in LABELS else "ports"
        parts.append("top holders " + ", ".join(
            f"{who(h)} ({fmt_count(h.get(protocol, h['ports']))} {label})" for h in holders[:3]))
    unread = sorted(p for p, state in (snap.get("probes") or {}).items() if state != "ok")
    if not parts:
        return f"snapshot {delay} found no holders: not confirmed (the burst may have drained)"
    text = f"snapshot {delay}: " + "; ".join(parts)
    if unread:
        text += f"; {'/'.join(unread)} table not read, not confirmed for it"
    return text


def peak_text(event: Dict[str, Any]) -> str:
    """Who peaked in the in-memory window before the event was read. A window that reached back to
    the event and saw a process rise by a quarter of the range (``window_confirm_percent``) names
    it; anything else says the holder is not confirmed and how little moved, never that the ports were fine, because a burst shorter than the window's tally can escape it."""
    peak = event.get("peak")
    if not peak:
        return "no peak window recorded, so the peak holder is not confirmed"
    label = LABELS.get(peak["protocol"], peak["protocol"])
    span = f"{fmt_count(round((peak['to'] or 0) - (peak['from'] or 0)))} s window"
    holders = peak.get("holders") or []
    if peak["confirmed"]:
        return f"peak {label} holders in the {span}: " + ", ".join(
            f"{who(h)} {fmt_count(h['peak'])} at {time.strftime('%H:%M:%S', time.localtime(h['at']))}"
            f" (from {fmt_count(h['low'])})" for h in [h for h in holders if h["peak"] - h["low"] >= peak["min_rise"]][:3])
    if not peak["samples"]:
        return f"the {span} held no readable {label} tally, so the peak holder is not confirmed"
    if not peak.get("covers_event"):
        return (f"peak holder not confirmed: the window starts after the event in the {span}; "
                "a burst shorter than the tally interval can fall between two tallies")
    # Events stored before largest_rise existed carry only the rows.
    rise = peak.get("largest_rise", max((h["peak"] - h["low"] for h in holders), default=0))
    return (f"peak holder not confirmed: largest rise {fmt_count(rise)} ports, too small to exhaust the range "
            f"in the {span}; a burst shorter than the tally interval can fall between two tallies")


_TRACE_STATES = {
    TraceState.NOT_ARMED: "trace not armed, so the sub-second holder is not confirmed",
    TraceState.UNSUPPORTED: "no trace reader on this platform, so the sub-second holder is not confirmed",
    TraceState.PENDING: "trace armed, its save after the event not read yet, so the sub-second holder is not confirmed",
    TraceState.NOT_SAVED: "trace armed but no saved trace covers the event, so the sub-second holder is not confirmed",
    TraceState.FAILED: "the saved trace could not be read, so the sub-second holder is not confirmed",
}


def trace_text(event: Dict[str, Any]) -> str:
    """What the owner-armed socket trace says about the seconds before the event. Only an ``ok``
    read with events names anyone; every other state says the holder is not confirmed."""
    trace = event.get("trace")
    if not trace:
        return "no trace reading, so the sub-second holder is not confirmed"
    if trace["state"] != TraceState.OK:
        return _TRACE_STATES.get(trace["state"], f"trace {trace['state']}, so the sub-second holder is not confirmed")
    span = f"{fmt_count(round(trace['to'] - trace['from']))} s before the event"
    holders = trace.get("holders") or []
    if not holders:
        return (f"trace held no socket events in the {span}: not confirmed "
                "(the circular trace may have wrapped past it)")
    rows = []
    for h in holders[:3]:
        counts = ", ".join(f"{fmt_count(h[k])} {label}" for k, label in
                           (("bind", "binds"), ("close", "closes"), ("create", "creates"),
                            ("reserve", "reservations"), ("reserve_failed", "failed reservations")) if h.get(k))
        rows.append(f"{who(h)} ({counts})")
    text = f"trace, {span}: " + "; ".join(rows)
    if trace.get("truncated"):
        text += f" (first {fmt_count(trace['events'])} events only)"
    return text


def _event_findings(events: Sequence[Dict[str, Any]], history: Sequence[Dict[str, Any]],
                    cfg: Dict[str, Any], now: float) -> List[Dict[str, Any]]:
    since = now - float(cfg["event_hold_hours"]) * _HOUR
    recent = sorted((e for e in events if e["ts"] >= since), key=lambda e: e["ts"], reverse=True)
    return [
        {"level": "watch" if e["event_id"] == _CHURN_EVENT and not _churn_at_act_line(history, cfg, e["ts"])
                 else "act",
         "kind": "system", "metric": f"tcpip_{e['event_id']}", "value": e["event_id"],
         "process": None, "ts": e["ts"],
         "reason": f"Windows logged {e['reason']} (Tcpip {e['event_id']}) at {_clock(e['ts'])}"
                   + (f"; {snapshot_text(e)}; {peak_text(e)}; {trace_text(e)}"
                      if e["event_id"] in SNAPSHOT_EVENTS else ""),
         "fix": RUNBOOK_FIX, **({"snapshot": e["snapshot"]} if e.get("snapshot") else {}),
         **({"peak": e["peak"]} if e.get("peak") else {}),
         **({"trace": e["trace"]} if e.get("trace") else {})}
        for e in recent
    ]


def _probe_findings(latest: Dict[str, Any], events_probe: Optional[str]) -> List[Dict[str, Any]]:
    probes = dict(latest.get("probes", {}))
    if events_probe:
        probes["port_events"] = events_probe
    return [
        {"level": "unknown", "kind": "probe", "metric": probe,
         "reason": f"The {probe} port probe failed, so ports are not confirmed fine", "fix": None}
        for probe, state in sorted(probes.items()) if state == "failed"  # state-literal-exempt: one probe's reading (ok, failed, denied), re-read every sample, never a lifecycle
    ]


def is_stale(history: Sequence[Dict[str, Any]], cfg: Dict[str, Any], now: float) -> bool:
    return not history or now - history[-1]["ts"] > float(cfg["interval_s"]) * float(cfg["stale_after_intervals"])


def findings(history: Sequence[Dict[str, Any]], events: Sequence[Dict[str, Any]], cfg: Dict[str, Any],
             now: float, events_probe: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every port finding as of ``now``; ``cfg`` is the ``ports`` section plus ``attribution_share``
    and ``reboot_only_processes``."""
    out = _event_findings(events, history, cfg, now)
    if is_stale(history, cfg, now):
        age = "never" if not history else f"{fmt_count((now - history[-1]['ts']) / 60)} min ago"
        out.append({"level": "unknown", "kind": "probe", "metric": "ports",
                    "reason": f"Last port sample: {age}, so ports are not confirmed fine", "fix": None})
        return out
    out += _probe_findings(history[-1], events_probe)
    for protocol in ("tcp", "udp"):
        found = _pressure_finding(history, cfg, now, f"{protocol}_percent", _percent(protocol),
                                  f"{LABELS[protocol]} dynamic ports", "% in use", protocol)
        if found:
            out.append(found)
    found = _pressure_finding(history, cfg, now, "tcp_time_wait", _time_wait, "TCP time wait", " sockets", None)
    if found:
        out.append(found)
    return out


def _slope(points: List[Tuple[float, float]]) -> Optional[float]:
    if len(points) < 2 or points[-1][0] - points[0][0] < _MIN_GROWTH_SPAN_S:
        return None
    mx = sum(t for t, _ in points) / len(points)
    my = sum(v for _, v in points) / len(points)
    var = sum((t - mx) ** 2 for t, _ in points)
    return sum((t - mx) * (v - my) for t, v in points) / var * _HOUR


def offenders(history: Sequence[Dict[str, Any]], now: float, per_list: int = 5) -> List[Dict[str, Any]]:
    """The newest sample's top port holders, each with its growth per hour over the last hour."""
    if not history:
        return []
    series: Dict[Tuple[int, str], List[Tuple[float, float]]] = {}
    for sample in history:
        if sample["ts"] < now - _HOUR:
            continue
        for h in sample.get("holders", []):
            series.setdefault((h["pid"], h["name"]), []).append((sample["ts"], float(h["ports"])))
    out = []
    for h in history[-1].get("holders", [])[:per_list]:
        rate = _slope(series.get((h["pid"], h["name"]), []))
        out.append({"name": h["name"], "label": h.get("label"), "pid": h["pid"], "value": h["ports"], "tcp": h["tcp"], "udp": h["udp"],
                    "rate_per_hour": None if rate is None else round(rate, 1)})
    return out


def block(history: Sequence[Dict[str, Any]], events: Sequence[Dict[str, Any]], cfg: Dict[str, Any],
          now: float, events_probe: Optional[str] = None) -> Dict[str, Any]:
    """The ``ports`` block of the health endpoint."""
    latest = history[-1] if history else {}
    hour = [s for s in history if s["ts"] >= now - _HOUR]
    out: Dict[str, Any] = {
        "sampled_at": latest.get("ts"), "sample_ms": latest.get("sample_ms"), "cpu_ms": latest.get("cpu_ms"),
        "interval_s": cfg["interval_s"], "samples": len(history), "stale": is_stale(history, cfg, now),
        "coverage": {**latest.get("probes", {}), **({"port_events": events_probe} if events_probe else {})},
    }
    for protocol in ("tcp", "udp"):
        current = latest.get(protocol) or {}
        worst = _peak(hour, _percent(protocol), now - _HOUR)
        out[protocol] = {"percent": current.get("percent"), "used": current.get("used"),
                         "total": current.get("total"), "range": current.get("range"),
                         "worst_hour_percent": worst[0] if worst else None,
                         "worst_hour_at": worst[1]["ts"] if worst else None}
    worst_tw = _peak(hour, _time_wait, now - _HOUR)
    out["tcp"]["time_wait"] = (latest.get("tcp") or {}).get("time_wait")
    out["tcp"]["worst_hour_time_wait"] = worst_tw[0] if worst_tw else None
    out["top_holders"] = offenders(history, now)
    last = max(events, key=lambda e: e["ts"]) if events else None
    out["last_event"] = ({k: last[k] for k in ("ts", "event_id", "protocol", "reason")} if last else None)
    if last and last["event_id"] in SNAPSHOT_EVENTS:
        out["last_event"]["snapshot"] = last.get("snapshot")
        out["last_event"]["snapshot_summary"] = snapshot_text(last)
        out["last_event"]["peak"] = last.get("peak")
        out["last_event"]["peak_summary"] = peak_text(last)
        out["last_event"]["trace"] = last.get("trace")
        out["last_event"]["trace_summary"] = trace_text(last)
    return out
