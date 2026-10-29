"""One health-watch sample: system pressure plus the top processes per metric.

Each probe reports its own state in ``probes`` — ``ok``, ``failed`` (it
should have worked and did not, so the verdict cannot be ``ok``),
``denied`` (the platform withholds it from an unprivileged user) or
``unsupported`` (no such counter here, e.g. commit charge on macOS). A metric
a probe could not read is ``None``, never ``0``.

Only the top ``top_n`` processes by each metric are kept: a leak worth
flagging is near the top of its metric long before it matters, and the
history stays small.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil

from . import win_probe

logger = logging.getLogger(__name__)

PROCESS_METRICS = ("handles", "memory_mb", "gdi", "user", "threads")
_DEFAULT_PORT_RANGE = (49152, 65535)
_MB = 1024 ** 2

_port_range: Optional[Tuple[int, int]] = None


def _ephemeral_range() -> Tuple[int, int]:
    """The ephemeral port range, read once per hub process (it is a reboot-scope setting)."""
    global _port_range
    if _port_range is None:
        found: Optional[Tuple[int, int]] = None
        if sys.platform == "win32":
            found = win_probe.dynamic_port_range()
        elif sys.platform.startswith("linux"):
            try:
                lo, hi = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()
                found = (int(lo), int(hi))
            except (OSError, ValueError):
                found = None
        _port_range = found or _DEFAULT_PORT_RANGE
    return _port_range


def _windows_processes() -> List[Dict[str, Any]]:
    rows = win_probe.process_table()
    gui = win_probe.gui_resources([int(r["pid"]) for r in rows])
    out = []
    for r in rows:
        gdi_user = gui.get(int(r["pid"]))
        out.append({
            "key": f"{r['pid']}:{r['create_time']}",
            "pid": r["pid"],
            "name": r["name"],
            "handles": r["handles"],
            "memory_mb": round(int(r["private_bytes"]) / _MB, 1),
            "threads": r["threads"],
            "gdi": gdi_user[0] if gdi_user else None,
            "user": gdi_user[1] if gdi_user else None,
        })
    return out


def _posix_processes() -> List[Dict[str, Any]]:
    """psutil fallback: open file descriptors stand in for handles, RSS for memory."""
    out = []
    for p in psutil.process_iter(["pid", "name", "create_time", "num_fds", "num_threads", "memory_info"]):
        info = p.info
        mem = info.get("memory_info")
        out.append({
            "key": f"{info['pid']}:{info.get('create_time')}",
            "pid": info["pid"],
            "name": info.get("name") or "?",
            "handles": info.get("num_fds"),
            "memory_mb": round(mem.rss / _MB, 1) if mem else None,
            "threads": info.get("num_threads"),
            "gdi": None,
            "user": None,
        })
    return out


def _top_processes(rows: List[Dict[str, Any]], top_n: int, ignore: List[str]) -> List[Dict[str, Any]]:
    skip = {n.lower() for n in ignore}
    rows = [r for r in rows if str(r["name"]).lower() not in skip]
    keep: Dict[str, Dict[str, Any]] = {}
    for metric in PROCESS_METRICS:
        ranked = sorted(
            (r for r in rows if r.get(metric) is not None),
            key=lambda r: r[metric], reverse=True,
        )
        for r in ranked[:top_n]:
            keep[r["key"]] = r
    return list(keep.values())


def _tcp_stats(system: Dict[str, Any], probes: Dict[str, str]) -> None:
    lo, hi = _ephemeral_range()
    try:
        conns = psutil.net_connections("tcp")
    except psutil.AccessDenied:
        probes["tcp"] = "denied"
        return
    except OSError as exc:
        logger.info("ℹ️ health watch: tcp probe failed: %s", exc)
        probes["tcp"] = "failed"
        return
    in_use = {c.laddr.port for c in conns if c.raddr and c.laddr and lo <= c.laddr.port <= hi}
    system["tcp_time_wait"] = sum(1 for c in conns if c.status == psutil.CONN_TIME_WAIT)
    system["ephemeral_ports_used"] = len(in_use)
    system["ephemeral_ports_total"] = hi - lo + 1
    system["ephemeral_ports_percent"] = round(100.0 * len(in_use) / (hi - lo + 1), 1)
    probes["tcp"] = "ok"


def _system_disk() -> str:
    """The OS drive: where the pagefile and temp files live."""
    if sys.platform == "win32":
        drive = os.environ.get("SystemDrive")
        return drive + os.sep if drive else Path.home().anchor
    return "/"


def take_sample(*, top_n: int = 15, ignore: Optional[List[str]] = None) -> Dict[str, Any]:
    """Probe this machine once. Blocking: call it off the event loop."""
    started = time.perf_counter()
    system: Dict[str, Any] = {}
    probes: Dict[str, str] = {}
    windows = win_probe.available()

    processes: List[Dict[str, Any]] = []
    try:
        processes = _windows_processes() if windows else _posix_processes()
        probes["processes"] = "ok"
    except OSError as exc:
        logger.info("ℹ️ health watch: process probe failed: %s", exc)
        probes["processes"] = "failed"

    if windows:
        try:
            perf = win_probe.performance_info()
            physical = perf["physical_total_bytes"] or 1.0
            system.update(
                commit_gb=round(perf["commit_bytes"] / 1024 ** 3, 1),
                commit_limit_gb=round(perf["commit_limit_bytes"] / 1024 ** 3, 1),
                commit_percent=round(100.0 * perf["commit_bytes"] / (perf["commit_limit_bytes"] or 1.0), 1),
                paged_pool_mb=round(perf["paged_pool_bytes"] / _MB),
                paged_pool_percent=round(100.0 * perf["paged_pool_bytes"] / physical, 2),
                nonpaged_pool_mb=round(perf["nonpaged_pool_bytes"] / _MB),
                nonpaged_pool_percent=round(100.0 * perf["nonpaged_pool_bytes"] / physical, 2),
                total_handles=int(perf["handle_count"]),
            )
            probes["performance"] = "ok"
        except OSError as exc:
            logger.info("ℹ️ health watch: performance probe failed: %s", exc)
            probes["performance"] = "failed"
    else:
        probes["performance"] = "unsupported"

    try:
        vm = psutil.virtual_memory()
        system["ram_available_gb"] = round(vm.available / 1024 ** 3, 1)
        system["ram_available_percent"] = round(100.0 * vm.available / vm.total, 1)
        probes["memory"] = "ok"
    except OSError as exc:
        logger.info("ℹ️ health watch: memory probe failed: %s", exc)
        probes["memory"] = "failed"

    _tcp_stats(system, probes)

    try:
        du = psutil.disk_usage(_system_disk())
        system["disk_free_gb"] = round(du.free / 1024 ** 3, 1)
        system["disk_free_percent"] = round(100.0 * du.free / du.total, 1)
        probes["disk"] = "ok"
    except OSError as exc:
        logger.info("ℹ️ health watch: disk probe failed: %s", exc)
        probes["disk"] = "failed"

    try:
        system["uptime_seconds"] = round(time.time() - psutil.boot_time())
    except OSError:
        system["uptime_seconds"] = None

    return {
        "ts": time.time(),
        "platform": sys.platform,
        "system": system,
        "probes": probes,
        "processes": _top_processes(processes, top_n, ignore or []),
        "sample_ms": round((time.perf_counter() - started) * 1000, 1),
    }
