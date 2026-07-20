#!/usr/bin/env python3
"""Zero-install portable diagnostics sampler (issue #316).

Delivered to a hub-less machine over SSH and run without a checkout::

    ssh user@host "python3 - --duration-s 3600 --interval-s 15" < scripts/portable_capture.py > out.json

then ingested back into this hub's store with ``python -m src.diagnostics.ingest``.

The one design rule that keeps the fleet honest: **this script captures raw and
interprets nothing.** No attribution, no health rules, no summaries — those all
live in ``src/diagnostics/`` and run centrally at ingest time, so there is
exactly one analysis implementation no matter which machine the samples came
from. Everything here is deliberately duplicated from ``sampler.py`` /
``attribution.py`` / ``system_stats.py`` rather than imported: the whole point
is that it runs where ``src/`` does not exist. The duplication is the contract,
not drift — this file must import nothing from the project.

Output contract (one JSON document on **stdout**; all progress on stderr so an
``ssh ... > out.json`` redirect captures pure JSON):

    {
      "schema": "llm-hub-diagnostics-capture/1",
      "machine": "<--machine or hostname>", "hostname": ..., "os": ...,
      "platform": "windows|darwin|linux",   # picks the ingest's rule group
      "cpu_count": <logical cores>,          # per-process CPU normalizes to this
      "interval_s": ..., "started_at": ..., "ended_at": ...,
      "samples": [ { ts, cpu_percent, per_core, load_avg, ram, swap, disk,
                     disk_io, net_io, gpus, process_count, ports_denied,
                     processes: [...], ports: [...] }, ... ]
    }

Stdlib + ``psutil`` only. If ``psutil`` is missing the script exits non-zero
with a clear message rather than emitting a half-capture — a silently-ingested
partial reading is worse than a recorded gap.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time

try:
    import psutil
except ImportError:  # pragma: no cover - exercised on peers, not in unit tests
    sys.stderr.write(
        "portable_capture: psutil is required but not installed on this machine.\n"
        "Install it (e.g. `python3 -m pip install --user psutil`) and re-run.\n"
    )
    raise SystemExit(2)


SCHEMA = "llm-hub-diagnostics-capture/1"

# Kept in lockstep with src/diagnostics/sampler.py so a portable run and a
# native run measure the same way. See the notes there for why each value is
# what it is.
MIN_INTERVAL_S = 5.0
MAX_INTERVAL_S = 600.0
DEFAULT_INTERVAL_S = 15.0
MAX_DURATION_S = 24 * 3600.0
DEFAULT_DURATION_S = 3600.0
_CPU_WINDOW_S = 0.5          # system CPU measured over our own window, not interval=None
_MAX_CMDLINE = 400
_NVIDIA_SMI_TIMEOUT_S = 3.0
_GIB = 1024 ** 3

# Windows' "System Idle Process" reads as ncores x idle-fraction (~1400% on a
# quiet 16-core box) and would rank as the busiest thing on the machine. Matched
# by name, never PID 0 (that is `kernel_task` on macOS, which is real work).
# Mirror of attribution._IDLE_PROCESS_NAMES.
_IDLE_PROCESS_NAMES = {"system idle process"}


def _platform_token() -> str:
    """``windows`` | ``darwin`` | ``linux`` — names the OS rule group the
    ingest applies. Same mapping as attribution.current_platform()."""
    raw = sys.platform
    if raw.startswith("win"):
        return "windows"
    if raw == "darwin":
        return "darwin"
    return "linux"


def _trim_cmdline(parts) -> str:
    if not parts:
        return ""
    joined = " ".join(parts)
    return joined if len(joined) <= _MAX_CMDLINE else joined[: _MAX_CMDLINE - 1] + "…"


def _scan_processes():
    """Full per-process inventory for one tick — raw, un-attributed.

    ``exe`` stands in when ``cmdline`` is unreadable (another user's process on
    macOS): a different kernel call that stays readable, and the only thing the
    ingest's path rules have to match on. ``rss_bytes``/``cpu_percent`` come
    back **None** (never 0) when psutil is denied — ``ad_value=None`` guarantees
    it — which is exactly what lets coverage tell 'denied' from a real zero."""
    out = []
    fields = ["pid", "ppid", "name", "cmdline", "exe", "cpu_percent",
              "memory_info", "num_threads", "status", "create_time"]
    for proc in psutil.process_iter(fields, ad_value=None):
        try:
            info = proc.info
            name = info.get("name") or ""
            if name.strip().lower() in _IDLE_PROCESS_NAMES:
                continue
            cmdline = _trim_cmdline(info.get("cmdline")) or (info.get("exe") or "")
            mem = info.get("memory_info")
            out.append({
                "pid": info.get("pid"),
                "ppid": info.get("ppid"),
                "name": name,
                "cmdline": cmdline,
                "cpu_percent": info.get("cpu_percent"),
                "rss_bytes": getattr(mem, "rss", None) if mem else None,
                "num_threads": info.get("num_threads"),
                "status": info.get("status"),
                "create_time": info.get("create_time"),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception as exc:  # noqa: BLE001 - one bad process never kills a run
            sys.stderr.write(f"portable_capture: skipped a process: {exc}\n")
            continue
    return out


def _scan_ports(processes):
    """Listening sockets with their owning pid/name — raw, un-attributed.

    Returns ``(rows, denied)``. On ``AccessDenied`` (needs root/sudo on macOS to
    see other users' sockets) it degrades to an empty list but reports
    ``denied=True``, so the ingest records a coverage gap instead of letting a
    blind scan read as 'nothing listening' (#322)."""
    by_pid = {p.get("pid"): p for p in processes}
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        return [], True
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"portable_capture: net_connections failed: {exc}\n")
        return [], False

    out, seen = [], set()
    for conn in conns:
        if conn.status != psutil.CONN_LISTEN or not conn.laddr:
            continue
        port = getattr(conn.laddr, "port", None)
        if port is None:
            continue
        proto = "udp" if conn.type == getattr(psutil, "SOCK_DGRAM", None) else "tcp"
        key = (port, proto, conn.pid)
        if key in seen:
            continue
        seen.add(key)
        owner = by_pid.get(conn.pid) or {}
        out.append({
            "port": port,
            "proto": proto,
            "address": getattr(conn.laddr, "ip", "") or "",
            "pid": conn.pid,
            "name": owner.get("name") or _name_for_pid(conn.pid),
        })
    return out, False


def _name_for_pid(pid):
    if not pid:
        return ""
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _io_counters(fn):
    try:
        counters = fn()
    except Exception:  # noqa: BLE001
        return {}
    if counters is None:
        return {}
    try:
        return {k: v for k, v in counters._asdict().items() if isinstance(v, (int, float))}
    except AttributeError:
        return {}


def _ram_stats():
    vm = psutil.virtual_memory()
    return {"used_gb": round(vm.used / _GIB, 2), "total_gb": round(vm.total / _GIB, 2),
            "percent": float(vm.percent)}


def _swap_stats():
    try:
        sw = psutil.swap_memory()
    except Exception:  # noqa: BLE001
        return {}
    return {"used_gb": round(sw.used / _GIB, 2), "total_gb": round(sw.total / _GIB, 2),
            "percent": float(sw.percent)}


def _disk_stats():
    root = "C:\\" if sys.platform == "win32" else "/"
    try:
        du = psutil.disk_usage(root)
    except OSError:
        return {}
    return {"used_gb": round(du.used / _GIB, 2), "total_gb": round(du.total / _GIB, 2),
            "percent": float(du.percent)}


def _gpu_stats():
    """Best-effort per-GPU snapshot via nvidia-smi; [] when it is absent
    (the Mac/laptop) or errors. Mirror of system_stats.gpu_stats()."""
    creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_NVIDIA_SMI_TIMEOUT_S,
            check=True, creationflags=creationflags,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []
    gpus = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        name, used_str, total_str, util_str = parts[:4]
        used_mb, total_mb, util = _to_float(used_str), _to_float(total_str), _to_float(util_str)
        vram_percent = None
        if used_mb is not None and total_mb and total_mb > 0:
            vram_percent = round((used_mb / total_mb) * 100.0, 1)
        gpus.append({"name": name, "used_mb": used_mb, "total_mb": total_mb,
                     "vram_percent": vram_percent, "util_percent": util})
    return gpus


def _to_float(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _prime_cpu_percent():
    """Prime psutil's CPU caches (system + per-process) so the first tick reads
    real usage rather than the 0.0 a cold call returns. Mirror of
    attribution.prime_cpu_percent()."""
    try:
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
    except Exception:  # noqa: BLE001
        pass
    for proc in psutil.process_iter(["pid"]):
        try:
            proc.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:  # noqa: BLE001
            continue


def _collect_tick():
    processes = _scan_processes()
    ports, ports_denied = _scan_ports(processes)
    try:
        per_core = [float(x) for x in psutil.cpu_percent(interval=_CPU_WINDOW_S, percpu=True)]
        cpu_total = round(sum(per_core) / len(per_core), 1) if per_core else None
    except Exception:  # noqa: BLE001
        per_core, cpu_total = [], None
    load_avg = None
    if hasattr(psutil, "getloadavg"):
        try:
            load_avg = [float(x) for x in psutil.getloadavg()]
        except (OSError, AttributeError):
            load_avg = None
    return {
        "ts": time.time(),
        "cpu_percent": cpu_total,
        "per_core": per_core,
        "load_avg": load_avg,
        "ram": _ram_stats(),
        "swap": _swap_stats(),
        "disk": _disk_stats(),
        "disk_io": _io_counters(psutil.disk_io_counters),
        "net_io": _io_counters(psutil.net_io_counters),
        "gpus": _gpu_stats(),
        "process_count": len(processes),
        "ports_denied": ports_denied,
        "processes": processes,
        "ports": ports,
    }


def _capture(*, interval_s, duration_s, samples, machine):
    interval = max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, float(interval_s)))
    started = time.time()
    _prime_cpu_percent()
    # Let the per-process CPU cache settle so the very first tick isn't 0.0
    # (mirrors one_shot's settle).
    time.sleep(1.0)

    collected = []
    deadline = None if samples else started + max(interval, min(MAX_DURATION_S, float(duration_s)))
    target_n = samples if samples else None

    while True:
        tick_started = time.time()
        if deadline is not None and tick_started >= deadline:
            break
        collected.append(_collect_tick())
        sys.stderr.write(f"portable_capture: sample {len(collected)}"
                         f"{'/' + str(target_n) if target_n else ''}\n")
        if target_n is not None and len(collected) >= target_n:
            break
        elapsed = time.time() - tick_started
        sleep_for = max(0.0, interval - elapsed)
        if deadline is not None:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            sleep_for = min(sleep_for, remaining)
        time.sleep(sleep_for)

    return {
        "schema": SCHEMA,
        "machine": machine or socket.gethostname(),
        "hostname": socket.gethostname(),
        "os": _os_name(),
        "platform": _platform_token(),
        "cpu_count": _cpu_count(),
        "interval_s": interval,
        "started_at": started,
        "ended_at": time.time(),
        "samples": collected,
    }


def _os_name():
    import platform as _pf
    return f"{_pf.system()} {_pf.release()}".strip()


def _cpu_count():
    try:
        return int(psutil.cpu_count(logical=True) or 1)
    except Exception:  # noqa: BLE001
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Portable diagnostics sampler (#316)")
    parser.add_argument("--duration-s", type=float, default=DEFAULT_DURATION_S,
                        help="total capture length in seconds (ignored if --samples given)")
    parser.add_argument("--interval-s", type=float, default=DEFAULT_INTERVAL_S,
                        help="seconds between samples")
    parser.add_argument("--samples", type=int, default=None,
                        help="exact sample count to take (e.g. 1 for a one-shot); "
                             "overrides --duration-s")
    parser.add_argument("--machine", default=None,
                        help="fleet machine id to stamp on the run "
                             "(defaults to this host's hostname)")
    args = parser.parse_args(argv)

    if args.samples is not None and args.samples < 1:
        parser.error("--samples must be >= 1")

    payload = _capture(interval_s=args.interval_s, duration_s=args.duration_s,
                       samples=args.samples, machine=args.machine)
    json.dump(payload, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    sys.stderr.write(f"portable_capture: done — {len(payload['samples'])} sample(s) "
                     f"for {payload['machine']} ({payload['platform']})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
