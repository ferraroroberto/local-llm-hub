"""Summaries, baseline drift, and the markdown health report (issue #315).

The UI is a trigger plus a digest — deep analysis happens against the SQLite
file or by pasting :func:`markdown_report` into an LLM session. This module is
the boundary between "rows" and "something a human reads".

:func:`drift` is the long-term-maintenance core: comparing a run against a
marked baseline turns creeping bloat ("when did this box get slow?") into a
reviewable diff — new resident apps, idle RAM growth, new listening ports.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import store

TOP_N = 12

# Human labels for the coverage section's collector rows (#322).
_COVERAGE_LABEL = {
    "ports": "Listening ports",
    "proc_mem": "Per-process memory",
    "proc_cpu": "Per-process CPU",
    "gpu": "GPU / VRAM",
}


def _mb(value: Optional[float]) -> float:
    return round((value or 0) / (1024 ** 2), 1)


def _cpu_count_of(run: Dict[str, Any]) -> int:
    """Core count for the machine that produced this run.

    Read from the run's stored params so a summary computed on a *different*
    machine (an exported/merged DB) still normalizes against the hardware the
    capture actually came from; falls back to this machine's count."""
    stored = (run.get("params") or {}).get("cpu_count")
    try:
        if stored:
            return max(1, int(stored))
    except (TypeError, ValueError):
        pass
    try:
        import psutil
        return max(1, int(psutil.cpu_count(logical=True) or 1))
    except Exception:  # noqa: BLE001
        return 1


def _machine_cpu(summed_percent: Optional[float], cpu_count: int) -> float:
    """Convert a sum of per-process CPU percentages into percent-of-machine.

    ``psutil`` reports per-process CPU relative to **one core**, so summing an
    app's processes on a 16-core box can legitimately exceed 100% — rendering
    that raw next to a 25% machine-wide figure reads as a bug. Dividing by the
    core count puts both on the same scale."""
    return round((summed_percent or 0) / cpu_count, 1)


def _mean(values: List[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 1) if clean else None


def _peak(values: List[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return round(max(clean), 1) if clean else None


def summary(run_id: str) -> Optional[Dict[str, Any]]:
    """The digest the modal renders: run metadata, resource envelope, top
    consumers by app and by process, the per-app process-count timeline, the
    listening-port table, and the persisted verdict."""
    run = store.get_run(run_id)
    if run is None:
        return None

    rows = store.samples(run_id)
    apps = store.app_aggregates(run_id)
    procs = store.process_aggregates(run_id)
    ports = store.listening_ports(run_id)

    cpu = [r.get("cpu_percent") for r in rows]
    ram = [r.get("ram_percent") for r in rows]
    swap = [r.get("swap_percent") for r in rows]
    disk = [r.get("disk_percent") for r in rows]
    counts = [r.get("process_count") for r in rows]

    gpu_peak: Dict[str, float] = {}
    for row in rows:
        for idx, gpu in enumerate(row.get("gpus") or []):
            pct = gpu.get("vram_percent")
            if pct is None:
                continue
            key = gpu.get("name") or f"GPU {idx + 1}"
            gpu_peak[key] = max(gpu_peak.get(key, 0.0), float(pct))

    cores = _cpu_count_of(run)
    return {
        "run": run,
        "cpu_count": cores,
        "resources": {
            "cpu": {"avg": _mean(cpu), "peak": _peak(cpu)},
            "ram": {"avg": _mean(ram), "peak": _peak(ram)},
            "swap": {"avg": _mean(swap), "peak": _peak(swap)},
            "disk": {"avg": _mean(disk), "peak": _peak(disk)},
            "gpu_peak_vram": [{"name": k, "peak_percent": round(v, 1)} for k, v in gpu_peak.items()],
            "process_count": {"avg": _mean(counts), "peak": _peak(counts)},
        },
        "apps": [
            {
                "app_id": a["app_id"],
                "avg_procs": round(a.get("avg_procs") or 0, 1),
                "peak_procs": int(a.get("peak_procs") or 0),
                "avg_rss_mb": _mb(a.get("avg_rss")),
                "peak_rss_mb": _mb(a.get("peak_rss")),
                # Percent of the whole machine, not a sum of per-core figures.
                "avg_cpu": _machine_cpu(a.get("avg_cpu"), cores),
                "peak_cpu": _machine_cpu(a.get("peak_cpu"), cores),
            }
            for a in apps[:TOP_N]
        ],
        "top_processes_by_rss": [_proc_row(p, cores) for p in
                                 sorted(procs, key=lambda p: p.get("peak_rss") or 0, reverse=True)[:TOP_N]],
        "top_processes_by_cpu": [_proc_row(p, cores) for p in
                                 sorted(procs, key=lambda p: p.get("avg_cpu") or 0, reverse=True)[:TOP_N]],
        "process_timeline": store.process_count_timeline(run_id),
        "ports": ports,
        "coverage": run.get("coverage") or {},
        "verdict": {"level": run.get("verdict_level"), "findings": run.get("findings") or []},
    }


def _proc_row(p: Dict[str, Any], cores: int = 1) -> Dict[str, Any]:
    return {
        "app_id": p.get("app_id"),
        "name": p.get("name"),
        "cmdline": p.get("cmdline"),
        "pid_count": p.get("pid_count"),
        "avg_cpu": _machine_cpu(p.get("avg_cpu"), cores),
        "peak_cpu": _machine_cpu(p.get("peak_cpu"), cores),
        "avg_rss_mb": _mb(p.get("avg_rss")),
        "peak_rss_mb": _mb(p.get("peak_rss")),
        "peak_threads": p.get("peak_threads"),
    }


# ------------------------------------------------------------------- drift


def drift(run_id: str, baseline_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Compare a run against a baseline: what changed, and by how much.

    Resolves the baseline from the run's own machine when not named, so the
    comparison is never accidentally cross-machine."""
    run = store.get_run(run_id)
    if run is None:
        return None
    if baseline_id is None:
        base = store.baseline_run(run["machine_id"])
    else:
        base = store.get_run(baseline_id)
    if base is None or base["run_id"] == run_id:
        return {"baseline": None, "changes": [], "apps": [], "ports": []}

    base_id = base["run_id"]
    cur_apps = {a["app_id"]: a for a in store.app_aggregates(run_id)}
    base_apps = {a["app_id"]: a for a in store.app_aggregates(base_id)}

    app_rows: List[Dict[str, Any]] = []
    for app_id in sorted(set(cur_apps) | set(base_apps)):
        cur = cur_apps.get(app_id)
        old = base_apps.get(app_id)
        app_rows.append({
            "app_id": app_id,
            "status": "new" if old is None else ("gone" if cur is None else "changed"),
            "procs_now": int((cur or {}).get("peak_procs") or 0),
            "procs_before": int((old or {}).get("peak_procs") or 0),
            "rss_mb_now": _mb((cur or {}).get("peak_rss")),
            "rss_mb_before": _mb((old or {}).get("peak_rss")),
        })
    for row in app_rows:
        row["procs_delta"] = row["procs_now"] - row["procs_before"]
        row["rss_mb_delta"] = round(row["rss_mb_now"] - row["rss_mb_before"], 1)

    cur_ports = {(p["port"], p["proto"]): p for p in store.listening_ports(run_id)}
    base_ports = {(p["port"], p["proto"]): p for p in store.listening_ports(base_id)}
    port_rows = [
        {"port": k[0], "proto": k[1], "app_id": v.get("app_id"), "status": "new"}
        for k, v in sorted(cur_ports.items()) if k not in base_ports
    ] + [
        {"port": k[0], "proto": k[1], "app_id": v.get("app_id"), "status": "gone"}
        for k, v in sorted(base_ports.items()) if k not in cur_ports
    ]

    cur_sum = summary(run_id) or {}
    base_sum = summary(base_id) or {}
    changes = []
    for label, path in (("RAM peak %", ("ram", "peak")), ("CPU avg %", ("cpu", "avg")),
                        ("Disk peak %", ("disk", "peak")), ("Processes peak", ("process_count", "peak"))):
        now = ((cur_sum.get("resources") or {}).get(path[0]) or {}).get(path[1])
        before = ((base_sum.get("resources") or {}).get(path[0]) or {}).get(path[1])
        if now is None or before is None:
            continue
        changes.append({"label": label, "now": now, "before": before,
                        "delta": round(now - before, 1)})

    new_apps = [r["app_id"] for r in app_rows if r["status"] == "new"]
    return {
        "baseline": {"run_id": base_id, "started_at": base["started_at"],
                     "note": base.get("note", "")},
        "changes": changes,
        "apps": sorted(app_rows, key=lambda r: -abs(r["rss_mb_delta"])),
        "new_apps": new_apps,
        "ports": port_rows,
    }


# ---------------------------------------------------------------- markdown


def _ts(value: Optional[float]) -> str:
    if not value:
        return "—"
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def markdown_report(run_id: str) -> Optional[str]:
    """A self-contained health report — the LLM-ready analysis surface.

    Deliberately plain markdown with no repo-specific jargon, so it can be
    pasted straight into a Claude session and reasoned about cold."""
    data = summary(run_id)
    if data is None:
        return None
    from . import coverage as cov
    run = data["run"]
    res = data["resources"]
    verdict = data["verdict"]
    coverage_map = data.get("coverage") or {}
    d = drift(run_id)

    degraded = cov.is_degraded(coverage_map)
    verdict_line = (verdict.get("level") or "healthy").upper()
    if degraded:
        verdict_line += " · ⚠ partial coverage"

    lines: List[str] = []
    lines.append(f"# Machine diagnostics — {run.get('machine_id', 'unknown')}")
    lines.append("")
    lines.append(f"- **Run:** `{run_id}` ({run.get('trigger', 'manual')}, {run.get('status', '')})")
    lines.append(f"- **Machine:** {run.get('hostname') or run.get('machine_id')} · {run.get('os', '')}")
    lines.append(f"- **Window:** {_ts(run.get('started_at'))} → {_ts(run.get('ended_at'))}"
                 f" · {run.get('sample_count', 0)} samples at {run.get('interval_s', 0):.0f}s")
    lines.append(f"- **Verdict:** **{verdict_line}**")
    lines.append("")

    findings = verdict.get("findings") or []
    lines.append("## Findings")
    lines.append("")
    if not findings:
        lines.append("No threshold was crossed — the machine looks healthy for this window.")
    else:
        for f in findings:
            lines.append(f"- **{f['level']}** · `{f['rule']}` — {f['summary']}")
    lines.append("")

    # Coverage — what we could and couldn't measure. Rendered whenever a
    # collector was blind, so a vanished section or a summed-over-nulls total
    # never masquerades as a complete reading (#322).
    if degraded:
        lines.append("## Coverage")
        lines.append("")
        lines.append("Some signals could not be fully collected on this machine — the"
                     " verdict above reflects only what was measurable.")
        lines.append("")
        lines.append("| Collector | Status |")
        lines.append("| --- | --- |")
        for name in ("ports", "proc_mem", "proc_cpu", "gpu"):
            entry = coverage_map.get(name)
            if entry:
                lines.append(f"| {_COVERAGE_LABEL.get(name, name)} | {cov.describe(name, entry)} |")
        lines.append("")

    lines.append("## Resource envelope")
    lines.append("")
    lines.append("| Metric | Average | Peak |")
    lines.append("| --- | --- | --- |")
    for label, key in (("CPU %", "cpu"), ("RAM %", "ram"), ("Swap %", "swap"),
                       ("Disk %", "disk"), ("Processes", "process_count")):
        entry = res.get(key) or {}
        lines.append(f"| {label} | {entry.get('avg', '—')} | {entry.get('peak', '—')} |")
    for gpu in res.get("gpu_peak_vram") or []:
        lines.append(f"| VRAM % ({gpu['name']}) | — | {gpu['peak_percent']} |")
    lines.append("")

    lines.append("## Load by app")
    lines.append("")
    lines.append("| App | Peak procs | Peak RSS (MB) | Avg CPU (% of machine) |")
    lines.append("| --- | --- | --- | --- |")
    for app in data["apps"]:
        lines.append(f"| {app['app_id']} | {app['peak_procs']} | {app['peak_rss_mb']} | {app['avg_cpu']} |")
    mem = coverage_map.get("proc_mem") or {}
    if mem.get("status") == cov.PARTIAL:
        unread = max(0, (mem.get("total") or 0) - (mem.get("readable") or 0))
        lines.append("")
        lines.append(f"> ⚠ RSS/CPU undercount the machine: {unread} process(es) were not"
                     " readable (insufficient privileges) and contribute 0 to these totals.")
    lines.append("")

    lines.append("## Heaviest processes (by peak RSS)")
    lines.append("")
    lines.append("| App | Process | PIDs | Peak RSS (MB) | Avg CPU (% of machine) |")
    lines.append("| --- | --- | --- | --- | --- |")
    for p in data["top_processes_by_rss"]:
        lines.append(f"| {p['app_id']} | {p['name']} | {p['pid_count']} | {p['peak_rss_mb']} | {p['avg_cpu']} |")
    lines.append("")

    ports_status = cov.collector_status(coverage_map, "ports")
    if data["ports"]:
        lines.append("## Listening ports")
        lines.append("")
        lines.append("| Port | Proto | Owner | Process |")
        lines.append("| --- | --- | --- | --- |")
        for q in data["ports"]:
            lines.append(f"| {q['port']} | {q['proto']} | {q['app_id']} | {q.get('name') or '—'} |")
        lines.append("")
    elif ports_status == cov.DENIED:
        # The defect that started #322: an empty section here read as "this box
        # exposes nothing", when the truth was "we weren't allowed to look".
        lines.append("## Listening ports")
        lines.append("")
        lines.append("_Not collected — the hub lacks the privilege to enumerate sockets on"
                     " this machine. This is a coverage gap, not an empty result._")
        lines.append("")

    if d and d.get("baseline"):
        lines.append(f"## Drift vs baseline ({_ts(d['baseline']['started_at'])})")
        lines.append("")
        for change in d["changes"]:
            sign = "+" if change["delta"] >= 0 else ""
            lines.append(f"- {change['label']}: {change['before']} → {change['now']} ({sign}{change['delta']})")
        if d.get("new_apps"):
            lines.append(f"- New apps since baseline: {', '.join(d['new_apps'])}")
        for port in d.get("ports") or []:
            lines.append(f"- Port {port['port']}/{port['proto']} ({port['app_id']}): {port['status']}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"_Generated {_ts(time.time())} by local-llm-hub diagnostics._")
    return "\n".join(lines) + "\n"
