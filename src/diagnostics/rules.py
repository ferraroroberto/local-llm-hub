"""Health-verdict engine over a stored capture (issue #315).

Turns rows into an opinion: ``healthy`` / ``warning`` / ``critical`` plus a
list of findings, each carrying the evidence behind it so a verdict is never
an unexplained colour.

Every rule is a **pure function of already-stored rows** — no live-system
access — which is what makes the engine unit-testable against synthetic
fixtures and reproducible when re-run months later against an old capture.
Thresholds come from ``config/diagnostics_rules.json``, so tuning is a data
edit.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import store

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "diagnostics_rules.json"

LEVELS = ("healthy", "warning", "critical")
_LEVEL_RANK = {"healthy": 0, "warning": 1, "critical": 2}

_DEFAULTS: Dict[str, Any] = {
    "cpu": {"sustained_percent_warn": 85, "sustained_percent_critical": 95,
            "sustained_fraction_of_run": 0.5},
    "ram": {"percent_warn": 85, "percent_critical": 95},
    "swap": {"percent_warn": 40, "percent_critical": 70},
    "disk": {"percent_warn": 85, "percent_critical": 95},
    "gpu": {"vram_percent_warn": 90, "vram_percent_critical": 97},
    "processes": {"total_count_warn": 700, "total_count_critical": 1000,
                  "per_app_count_warn": 25, "per_app_count_critical": 40,
                  "per_app_ignore": ["unattributed", "windows-services",
                                     "windows-shell", "macos-system", "linux-system",
                                     "shell", "chrome", "edge", "firefox", "webkit",
                                     "edge-webview", "qtwebengine"],
                  "unattributed_rss_mb_warn": 500, "zombie_count_warn": 5},
    "ports": {"duplicate_listener_warn": True},
}

_rules_cache: Dict[str, Any] = {}
_rules_path: Optional[Path] = None


def set_rules_path(path: Optional[Path]) -> None:
    global _rules_path
    _rules_path = Path(path) if path else None
    _rules_cache.clear()


def reload_thresholds() -> Dict[str, Any]:
    """Drop the cache and re-read the file — so retuning
    ``config/diagnostics_rules.json`` and re-judging an old run needs no
    hub restart."""
    _rules_cache.clear()
    return load_thresholds()


def load_thresholds() -> Dict[str, Any]:
    """Load thresholds, merging the file over the built-in defaults so a
    partial config file only overrides what it names."""
    if _rules_cache:
        return _rules_cache
    target = _rules_path or DEFAULT_RULES_PATH
    merged = {k: dict(v) for k, v in _DEFAULTS.items()}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for section, values in data.items():
                    if section.startswith("_") or not isinstance(values, dict):
                        continue
                    merged.setdefault(section, {}).update(values)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("⚠️ could not load %s: %s", target.name, exc)
    _rules_cache.update(merged)
    return _rules_cache


def _finding(rule: str, level: str, summary: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {"rule": rule, "level": level, "summary": summary, "evidence": evidence}


def _worst(findings: List[Dict[str, Any]]) -> str:
    level = "healthy"
    for f in findings:
        if _LEVEL_RANK.get(f["level"], 0) > _LEVEL_RANK[level]:
            level = f["level"]
    return level


def _pct_level(value: Optional[float], warn: float, crit: float) -> Optional[str]:
    if value is None:
        return None
    if value >= crit:
        return "critical"
    if value >= warn:
        return "warning"
    return None


# ------------------------------------------------------------------- rules


def _cpu_findings(rows: List[Dict[str, Any]], th: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Sustained CPU pressure — a *fraction of the run* above the threshold,
    not a single spike. One 100% tick during a model load is normal; half the
    run pinned is the actual problem."""
    values = [r["cpu_percent"] for r in rows if r.get("cpu_percent") is not None]
    if not values:
        return []
    cfg = th["cpu"]
    fraction = float(cfg.get("sustained_fraction_of_run", 0.5))
    out: List[Dict[str, Any]] = []
    for level, key in (("critical", "sustained_percent_critical"),
                       ("warning", "sustained_percent_warn")):
        limit = float(cfg[key])
        over = [v for v in values if v >= limit]
        if len(over) >= max(1, int(len(values) * fraction)):
            out.append(_finding(
                "cpu.sustained", level,
                f"CPU stayed at or above {limit:.0f}% for {len(over)} of {len(values)} samples",
                {"threshold_percent": limit, "samples_over": len(over),
                 "samples_total": len(values), "peak_percent": round(max(values), 1),
                 "avg_percent": round(sum(values) / len(values), 1)},
            ))
            break  # critical supersedes warning for the same rule
    return out


def _simple_pressure(rows, th, column, section, rule, label, unit="%") -> List[Dict[str, Any]]:
    """Peak-based pressure rule shared by RAM / swap / disk."""
    values = [r[column] for r in rows if r.get(column) is not None]
    if not values:
        return []
    peak = max(values)
    cfg = th[section]
    warn = float(cfg.get("percent_warn", 999))
    crit = float(cfg.get("percent_critical", 999))
    level = _pct_level(peak, warn, crit)
    if not level:
        return []
    return [_finding(
        rule, level, f"{label} peaked at {peak:.0f}{unit}",
        {"peak_percent": round(peak, 1), "warn": warn, "critical": crit,
         "avg_percent": round(sum(values) / len(values), 1)},
    )]


def _gpu_findings(rows: List[Dict[str, Any]], th: Dict[str, Any]) -> List[Dict[str, Any]]:
    cfg = th["gpu"]
    warn = float(cfg.get("vram_percent_warn", 999))
    crit = float(cfg.get("vram_percent_critical", 999))
    peaks: Dict[str, float] = {}
    for row in rows:
        for idx, gpu in enumerate(row.get("gpus") or []):
            pct = gpu.get("vram_percent")
            if pct is None:
                continue
            key = gpu.get("name") or f"GPU {idx + 1}"
            peaks[key] = max(peaks.get(key, 0.0), float(pct))
    out: List[Dict[str, Any]] = []
    for name, peak in peaks.items():
        level = _pct_level(peak, warn, crit)
        if level:
            out.append(_finding(
                "gpu.vram", level, f"{name} VRAM peaked at {peak:.0f}%",
                {"gpu": name, "peak_percent": round(peak, 1), "warn": warn, "critical": crit},
            ))
    return out


def _process_findings(
    rows: List[Dict[str, Any]],
    apps: List[Dict[str, Any]],
    procs: List[Dict[str, Any]],
    th: Dict[str, Any],
) -> List[Dict[str, Any]]:
    cfg = th["processes"]
    out: List[Dict[str, Any]] = []

    counts = [r["process_count"] for r in rows if r.get("process_count") is not None]
    if counts:
        peak = max(counts)
        level = _pct_level(float(peak), float(cfg["total_count_warn"]),
                           float(cfg["total_count_critical"]))
        if level:
            out.append(_finding(
                "processes.total", level, f"{peak} processes running at peak",
                {"peak_count": peak, "warn": cfg["total_count_warn"],
                 "critical": cfg["total_count_critical"]},
            ))

    # Per-app process-count ceilings — the "am I stacking too much on one
    # app" signal, expressed per app rather than per binary name.
    #
    # Aggregate buckets are excluded: `unattributed`, `windows-services` and
    # friends are *collections* of unrelated processes, not one app, so their
    # size says nothing about a single app being out of control. Judging them
    # here made a healthy box report critical on every run. Total process
    # count and the unattributed-RSS rule already cover what they do signal.
    ignored = {str(a).lower() for a in cfg.get("per_app_ignore", [])}
    for app in apps:
        app_id = app.get("app_id") or "unattributed"
        if app_id.lower() in ignored:
            continue
        peak_procs = int(app.get("peak_procs") or 0)
        level = _pct_level(float(peak_procs), float(cfg["per_app_count_warn"]),
                           float(cfg["per_app_count_critical"]))
        if level:
            out.append(_finding(
                "processes.per_app", level,
                f"{app_id} peaked at {peak_procs} concurrent processes",
                {"app_id": app_id, "peak_procs": peak_procs,
                 "warn": cfg["per_app_count_warn"], "critical": cfg["per_app_count_critical"],
                 "peak_rss_mb": round((app.get("peak_rss") or 0) / (1024 ** 2), 1)},
            ))

    # Heavyweight processes nobody has accounted for — the bloat review list.
    floor_mb = float(cfg.get("unattributed_rss_mb_warn", 500))
    heavy = [
        p for p in procs
        if (p.get("app_id") == "unattributed")
        and ((p.get("peak_rss") or 0) / (1024 ** 2)) >= floor_mb
    ]
    if heavy:
        top = sorted(heavy, key=lambda p: p.get("peak_rss") or 0, reverse=True)[:5]
        out.append(_finding(
            "processes.unattributed", "warning",
            f"{len(heavy)} unattributed process group(s) above {floor_mb:.0f} MB",
            {"count": len(heavy), "floor_mb": floor_mb, "top": [
                {"name": p.get("name"), "peak_rss_mb": round((p.get("peak_rss") or 0) / (1024 ** 2), 1)}
                for p in top
            ]},
        ))

    zombie_limit = int(cfg.get("zombie_count_warn", 5))
    zombies = [p for p in procs if (p.get("name") and _is_zombie(p))]
    if len(zombies) >= zombie_limit:
        out.append(_finding(
            "processes.zombies", "warning",
            f"{len(zombies)} zombie/defunct process group(s) observed",
            {"count": len(zombies), "warn": zombie_limit},
        ))
    return out


def _is_zombie(proc_row: Dict[str, Any]) -> bool:
    return str(proc_row.get("status") or "").lower() in {"zombie", "defunct"}


def _port_findings(ports: List[Dict[str, Any]], th: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Two apps claiming the same port across a run means something is
    flapping — a restart loop, or two launchers fighting for a backend."""
    if not th["ports"].get("duplicate_listener_warn", True):
        return []
    by_port: Dict[int, set] = defaultdict(set)
    for row in ports:
        by_port[row["port"]].add(row.get("app_id") or "unattributed")
    dupes = {p: sorted(a) for p, a in by_port.items() if len(a) > 1}
    if not dupes:
        return []
    return [_finding(
        "ports.duplicate", "warning",
        f"{len(dupes)} port(s) claimed by more than one app during the run",
        {"ports": [{"port": p, "apps": a} for p, a in sorted(dupes.items())]},
    )]


# --------------------------------------------------------------- entrypoint


def evaluate(run_id: str) -> Dict[str, Any]:
    """Evaluate a stored run and return ``{level, findings}`` (no write)."""
    th = load_thresholds()
    rows = store.samples(run_id)
    if not rows:
        return {"level": "healthy", "findings": [], "sample_count": 0}

    apps = store.app_aggregates(run_id)
    procs = store.process_aggregates(run_id)
    ports = store.listening_ports(run_id)

    findings: List[Dict[str, Any]] = []
    findings += _cpu_findings(rows, th)
    findings += _simple_pressure(rows, th, "ram_percent", "ram", "ram.pressure", "RAM")
    findings += _simple_pressure(rows, th, "swap_percent", "swap", "swap.pressure", "Swap")
    findings += _simple_pressure(rows, th, "disk_percent", "disk", "disk.capacity", "Disk")
    findings += _gpu_findings(rows, th)
    findings += _process_findings(rows, apps, procs, th)
    findings += _port_findings(ports, th)

    findings.sort(key=lambda f: -_LEVEL_RANK.get(f["level"], 0))
    return {"level": _worst(findings), "findings": findings, "sample_count": len(rows)}


def evaluate_and_save(run_id: str) -> Dict[str, Any]:
    """Evaluate a run and persist the verdict. Called when a run finishes."""
    result = evaluate(run_id)
    try:
        store.save_verdict(run_id, result["level"], result["findings"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ could not save verdict for %s: %s", run_id, exc)
    return result
