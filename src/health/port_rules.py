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
    holder = _top_holder(at, protocol) if protocol else None
    used = (at.get(protocol) or {}).get("used") if protocol else None
    if holder:
        process = holder["name"]
        reason += f", mostly {process} ({fmt_count(holder[protocol])} ports)"
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
        parts.append("time wait mostly on " + ", ".join(
            f"port {g['port']} ({g['listener'] or 'not a local listener'}, {fmt_count(g['count'])} sockets)"
            for g in groups[:3]))
    holders = snap.get("holders") or []
    if holders:
        parts.append("top holders " + ", ".join(f"{h['name']} ({fmt_count(h['ports'])} ports)" for h in holders[:3]))
    unread = sorted(p for p, state in (snap.get("probes") or {}).items() if state != "ok")
    if not parts:
        return f"snapshot {delay} found no holders: not confirmed (the burst may have drained)"
    text = f"snapshot {delay}: " + "; ".join(parts)
    if unread:
        text += f"; {'/'.join(unread)} table not read, not confirmed for it"
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
                   + (f"; {snapshot_text(e)}" if e["event_id"] in SNAPSHOT_EVENTS else ""),
         "fix": RUNBOOK_FIX, **({"snapshot": e["snapshot"]} if e.get("snapshot") else {})}
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
        out.append({"name": h["name"], "pid": h["pid"], "value": h["ports"], "tcp": h["tcp"], "udp": h["udp"],
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
    return out
