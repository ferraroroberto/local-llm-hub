"""The health verdict: a pure function of the sample history and the config.

Levels rank ``ok`` < ``unknown`` < ``watch`` < ``act``: a known problem
outranks a blind spot, but a blind spot is never reported as ``ok``. The
suggested fix prefers restarting one process over a reboot; a reboot is
offered only for state a process restart cannot return (kernel pools, commit
or handles not held by one process, or a process Windows cannot restart on
its own).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

LEVEL_RANK = {"ok": 0, "unknown": 1, "watch": 2, "act": 3}

# metric -> (label, unit suffix, probe that reads it)
SYSTEM_METRICS: Dict[str, Tuple[str, str, str]] = {
    "commit_percent": ("Commit charge", "% of limit", "performance"),
    "ram_available_percent": ("Available RAM", "%", "memory"),
    "nonpaged_pool_percent": ("Nonpaged pool", "% of RAM", "performance"),
    "paged_pool_percent": ("Paged pool", "% of RAM", "performance"),
    "total_handles": ("System handles", "", "performance"),
    "tcp_time_wait": ("TCP TIME_WAIT", " sockets", "tcp"),
    "ephemeral_ports_percent": ("Ephemeral ports in use", "%", "tcp"),
    "disk_free_percent": ("System disk free", "%", "disk"),
}

PROCESS_UNITS = {
    "handles": "handles", "memory_mb": "MB", "gdi": "GDI objects",
    "user": "USER objects", "threads": "threads",
}

_PORT_FIX = "No reboot: TIME_WAIT drains in minutes; find the client opening connections in a burst"
_POOL_FIX = "Reboot: kernel pool memory is only returned by restarting Windows"


def fmt_count(n: float) -> str:
    """3.9 M / 90 k / 812 — short enough for a one-line reason."""
    a = abs(n)
    if a >= 1e6:
        return f"{n / 1e6:.1f} M"
    if a >= 1e4:
        return f"{n / 1e3:.0f} k"
    if a >= 100:
        return f"{n:.0f}"
    return f"{n:.1f}".rstrip("0").rstrip(".")


def growth_per_hour(series: Sequence[Tuple[float, float]], leak: Dict[str, Any]) -> Optional[float]:
    """Least-squares growth per hour of a steadily rising series, else ``None``.

    Steady means enough samples over enough time, mostly rising steps and a
    net gain, so one model load (a single step up) never reads as a leak."""
    if len(series) < int(leak["min_samples"]):
        return None
    t0 = series[0][0]
    xs = [(t - t0) / 3600.0 for t, _ in series]
    ys = [float(v) for _, v in series]
    if xs[-1] < float(leak["min_span_hours"]) or ys[-1] <= ys[0]:
        return None
    steps = [b - a for a, b in zip(ys, ys[1:])]
    if sum(1 for d in steps if d > 0) / len(steps) < float(leak["min_rising_fraction"]):
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    return slope if slope > 0 else None


def _restart_fix(name: str, cfg: Dict[str, Any]) -> str:
    reboot_only = {n.lower() for n in cfg.get("reboot_only_processes", [])}
    if name.lower() in reboot_only:
        return f"Reboot: {name} cannot be restarted on its own"
    return f"Restart {name}"


def _level(value: float, watch: Optional[float], act: Optional[float], below: bool) -> str:
    def past(limit: Optional[float]) -> bool:
        if limit is None:
            return False
        return value <= limit if below else value >= limit
    if past(act):
        return "act"
    if past(watch):
        return "watch"
    return "ok"


def _process_series(history: Sequence[Dict[str, Any]], cutoff: float) -> Dict[str, Dict[str, List[Tuple[float, float]]]]:
    series: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
    for sample in history:
        if sample["ts"] < cutoff:
            continue
        for proc in sample.get("processes", []):
            per = series.setdefault(proc["key"], {})
            for metric in PROCESS_UNITS:
                value = proc.get(metric)
                if value is not None:
                    per.setdefault(metric, []).append((sample["ts"], float(value)))
    return series


def _process_findings(history: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Findings for live processes, plus every tracked process's value and rate."""
    latest = history[-1]
    series = _process_series(history, latest["ts"] - float(cfg["leak"]["window_hours"]) * 3600)
    findings: List[Dict[str, Any]] = []
    tracked: List[Dict[str, Any]] = []
    for proc in latest.get("processes", []):
        for metric, rule in cfg["process"].items():
            value = proc.get(metric)
            if value is None or metric not in PROCESS_UNITS:
                continue
            rate = growth_per_hour(series.get(proc["key"], {}).get(metric, []), cfg["leak"])
            tracked.append({"name": proc["name"], "pid": proc["pid"], "metric": metric,
                            "value": value, "rate_per_hour": rate})
            level = _level(value, rule.get("watch_value"), rule.get("act_value"), False)
            if rate is not None and value >= float(rule.get("rate_min_value") or 0):
                rate_level = _level(rate, rule.get("watch_rate_per_hour"), rule.get("act_rate_per_hour"), False)
                if LEVEL_RANK[rate_level] > LEVEL_RANK[level]:
                    level = rate_level
            if level == "ok":
                continue
            unit = PROCESS_UNITS[metric]
            reason = f"{proc['name']} at {fmt_count(value)} {unit}"
            eta = None
            if rate is not None:
                reason += f", +{fmt_count(rate)}/h"
                act_value = rule.get("act_value")
                if act_value and value < act_value:
                    eta = round((act_value - value) / rate, 1)
                    reason += f", act level in {fmt_count(eta)} h"
            findings.append({
                "level": level, "kind": "process", "metric": metric,
                "process": proc["name"], "pid": proc["pid"], "value": value,
                "rate_per_hour": rate, "eta_hours": eta,
                "reason": reason, "fix": _restart_fix(proc["name"], cfg),
            })
    return findings, tracked


def _top(latest: Dict[str, Any], metric: str) -> Optional[Dict[str, Any]]:
    procs = [p for p in latest.get("processes", []) if p.get(metric) is not None]
    return max(procs, key=lambda p: p[metric]) if procs else None


def _system_fix(metric: str, latest: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """The suggested fix for a system finding and, when one process explains it, its name."""
    system = latest.get("system", {})
    share = float(cfg.get("attribution_share", 0.5))
    if metric == "total_handles":
        top = _top(latest, "handles")
        if top and system.get("total_handles") and top["handles"] / system["total_handles"] >= share:
            return _restart_fix(top["name"], cfg), top["name"]
        return "Reboot: no single process holds most of the handles", None
    if metric in ("commit_percent", "ram_available_percent"):
        top = _top(latest, "memory_mb")
        commit_gb = system.get("commit_gb")
        if top and commit_gb and top["memory_mb"] / 1024 / commit_gb >= share:
            return _restart_fix(top["name"], cfg), top["name"]
        if metric == "ram_available_percent":
            return "Close or restart the largest memory users", None
        return "Reboot: no single process holds most of the commit charge", None
    if metric in ("nonpaged_pool_percent", "paged_pool_percent"):
        return _POOL_FIX, None
    if metric in ("tcp_time_wait", "ephemeral_ports_percent"):
        return _PORT_FIX, None
    return "Free space on the system disk", None


def _system_findings(latest: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    system = latest.get("system", {})
    for metric, rule in cfg["system"].items():
        value = system.get(metric)
        if value is None or metric not in SYSTEM_METRICS:
            continue
        level = _level(float(value), rule.get("watch"), rule.get("act"), bool(rule.get("below")))
        if level == "ok":
            continue
        label, unit, _probe = SYSTEM_METRICS[metric]
        fix, process = _system_fix(metric, latest, cfg)
        reason = f"{label} {fmt_count(float(value))}{unit}"
        if process:
            reason += f", mostly {process}"
        findings.append({"level": level, "kind": "system", "metric": metric, "value": value,
                         "process": process, "reason": reason, "fix": fix})
    return findings


def _probe_findings(latest: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"level": "unknown", "kind": "probe", "metric": probe,
         "reason": f"The {probe} probe failed, so it is not confirmed fine", "fix": None}
        for probe, state in sorted(latest.get("probes", {}).items()) if state == "failed"
    ]


def _offenders(tracked: List[Dict[str, Any]], per_metric: int = 3) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for metric in PROCESS_UNITS:
        rows = sorted((t for t in tracked if t["metric"] == metric), key=lambda t: t["value"], reverse=True)
        if rows:
            out[metric] = [{k: t[k] for k in ("name", "pid", "value", "rate_per_hour")} for t in rows[:per_metric]]
    return out


def _unknown(reason: str, latest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "verdict": "unknown", "reason": reason, "fix": None,
        "findings": [{"level": "unknown", "kind": "probe", "metric": "sample", "reason": reason, "fix": None}],
        "offenders": {}, "system": (latest or {}).get("system", {}),
        "coverage": (latest or {}).get("probes", {}),
        "sampled_at": (latest or {}).get("ts"), "sample_ms": (latest or {}).get("sample_ms"),
    }


def classify(history: Sequence[Dict[str, Any]], cfg: Dict[str, Any], now: float) -> Dict[str, Any]:
    """The verdict over ``history`` (oldest first) as of ``now``."""
    if not history:
        return _unknown("No sample yet", None)
    latest = history[-1]
    age = now - float(latest["ts"])
    if age > float(cfg["interval_s"]) * float(cfg["stale_after_intervals"]):
        return _unknown(f"Last sample is {fmt_count(age / 60)} min old", latest)

    process_findings, tracked = _process_findings(history, cfg)
    findings = _probe_findings(latest) + _system_findings(latest, cfg) + process_findings
    findings.sort(key=lambda f: LEVEL_RANK[f["level"]], reverse=True)

    verdict = findings[0]["level"] if findings else "ok"
    if verdict == "ok":
        reason, fix = "No leak or pressure found", None
    else:
        worst = [f for f in findings if f["level"] == verdict]
        reason = worst[0]["reason"] + (f" (+{len(findings) - 1} more)" if len(findings) > 1 else "")
        fixes = [f["fix"] for f in worst if f["fix"]]
        restarts = [f for f in fixes if f.startswith("Restart ")]
        fix = (restarts or fixes or [None])[0]
    return {
        "verdict": verdict, "reason": reason, "fix": fix, "findings": findings,
        "offenders": _offenders(tracked), "system": latest.get("system", {}),
        "coverage": latest.get("probes", {}),
        "sampled_at": latest["ts"], "sample_ms": latest.get("sample_ms"),
    }
