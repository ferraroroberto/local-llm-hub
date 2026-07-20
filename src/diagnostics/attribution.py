"""Process → fleet-app attribution and the listening-port map (issue #315).

``python.exe × 14`` is noise. ``app-launcher: 3 procs / 800 MB`` is signal.
This module turns the former into the latter by reading each process's command
line and deciding which *app* owns it:

  1. **fleet root** — a path under a configured automation root
     (``E:/automation/<repo>/…``) attributes to ``<repo>``. This catches every
     sister project's ``.venv`` interpreter, which is how nearly all the
     fleet's Python processes actually launch.
  2. **known binary** — ``llama-server``, ``cloudflared``, ``dockerd``, browsers,
     OS services… mapped by executable name.
  3. **cmdline substring** — a few narrow rules for things neither of the above
     catches.
  4. **path prefix** — the broad net, matched last: an OS-owned directory
     (``/System/Library/``, ``/usr/libexec/``, ``C:/Windows/``) tells you the
     owner even when the executable's name means nothing on its own. This is
     what makes macOS and Linux legible (#320); enumerating Apple's several
     hundred daemon names by hand never would have been.
  5. **``unattributed``** — everything else. This bucket is not a failure mode;
     it is the review list of processes nobody has accounted for yet, which is
     precisely what the user is trying to find.

Rules live in ``config/diagnostics_apps.json`` so teaching the sampler a new
app is a data edit, never a code change. Any rule group may additionally carry
a ``_windows`` / ``_darwin`` / ``_linux`` suffixed twin, merged in only on that
platform — ``/usr/bin`` is Apple-owned on macOS but ordinary user software on
Linux, so a single cross-platform table cannot be right for both.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "diagnostics_apps.json"

UNATTRIBUTED = "unattributed"

_rules_cache: Dict[str, Any] = {}
_rules_path: Optional[Path] = None
_platform_override: Optional[str] = None


def set_rules_path(path: Optional[Path]) -> None:
    """Point attribution at another rules file (tests)."""
    global _rules_path
    _rules_path = Path(path) if path else None
    _rules_cache.clear()


def set_platform(platform: Optional[str]) -> None:
    """Force the platform used to select ``_windows``/``_darwin``/``_linux``
    rule groups. Tests only — it is the sole way to exercise the macOS and
    Linux tables from the Windows dev box (#320)."""
    global _platform_override
    _platform_override = platform
    _rules_cache.clear()


def current_platform() -> str:
    """``windows`` | ``darwin`` | ``linux`` — the suffix naming the OS-specific
    rule groups that apply here."""
    if _platform_override:
        return _platform_override
    raw = sys.platform
    if raw.startswith("win"):
        return "windows"
    if raw == "darwin":
        return "darwin"
    return "linux"


def _merge_for_platform(data: Dict[str, Any], key: str) -> Any:
    """Return ``data[key]`` merged with its ``<key>_<platform>`` twin.

    The OS-specific entries are applied *after* the shared ones, so a platform
    may deliberately override a shared rule as well as extend it."""
    shared = data.get(key)
    specific = data.get(f"{key}_{current_platform()}")
    if isinstance(shared, list) or isinstance(specific, list):
        return list(shared or []) + list(specific or [])
    merged: Dict[str, Any] = {}
    merged.update(shared or {})
    merged.update(specific or {})
    return merged


def load_rules() -> Dict[str, Any]:
    """Load + cache the attribution rules. A broken file degrades to empty
    rules (everything unattributed) rather than breaking a capture."""
    if _rules_cache:
        return _rules_cache
    target = _rules_path or DEFAULT_RULES_PATH
    data: Dict[str, Any] = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("⚠️ could not load %s: %s", target.name, exc)
    # Longest prefix first, so `/system/library/` is decided before `/system/`
    # and the table stays order-independent as it grows.
    path_prefixes = sorted(
        ((_norm(k), str(v)) for k, v in _merge_for_platform(data, "path_prefixes").items()),
        key=lambda kv: -len(kv[0]),
    )
    _rules_cache.update({
        "fleet_roots": [_norm(r) for r in _merge_for_platform(data, "fleet_roots") if r],
        "binaries": {
            str(k).lower(): str(v) for k, v in _merge_for_platform(data, "binaries").items()
        },
        "cmdline_contains": {
            _norm(k): str(v) for k, v in _merge_for_platform(data, "cmdline_contains").items()
        },
        "path_prefixes": path_prefixes,
    })
    return _rules_cache


def _norm(text: str) -> str:
    """Lowercase with forward slashes and ``~`` expanded, so one rule matches
    on every OS."""
    expanded = os.path.expanduser(str(text))
    return expanded.replace("\\", "/").lower()


def _strip_exe(name: str) -> str:
    lowered = name.lower()
    for suffix in (".exe", ".bat", ".cmd", ".com"):
        if lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


def attribute(name: str, cmdline: str) -> str:
    """Return the ``app_id`` owning a process.

    Fleet-root matching runs against the whole command line, not just the
    executable, so ``pythonw.exe -m tray`` launched from a repo's ``.venv``
    still attributes to that repo."""
    rules = load_rules()
    hay = _norm(cmdline or name or "")

    for root in rules["fleet_roots"]:
        marker = root.rstrip("/") + "/"
        idx = hay.find(marker)
        if idx >= 0:
            tail = hay[idx + len(marker):]
            repo = tail.split("/", 1)[0].strip()
            if repo:
                # A sibling worktree (repo-wt-315) is the same app as its repo.
                return repo.split("-wt-", 1)[0]

    binary = _strip_exe(name or "")
    mapped = rules["binaries"].get(binary)
    if mapped:
        return mapped

    for needle, app_id in rules["cmdline_contains"].items():
        if needle and needle in hay:
            return app_id

    # Broadest net, deliberately last: an OS-owned directory identifies the
    # owner of processes whose names are meaningless in isolation. Anchored at
    # the start of the executable path, never a substring search — `/bin/` as a
    # substring also matches `/opt/homebrew/bin/`, which would have swallowed
    # user-installed software into the system bucket and hidden the bloat this
    # capture exists to surface.
    exe_path = hay.lstrip('"')
    for prefix, app_id in rules["path_prefixes"]:
        if prefix and exe_path.startswith(prefix):
            return app_id

    return UNATTRIBUTED


# ------------------------------------------------------------- process scan

_MAX_CMDLINE = 400

# Windows' "System Idle Process" (PID 0) is a bookkeeping placeholder for idle
# cycles, not a process: psutil reports its CPU as ncores x idle-fraction, so on
# a quiet 16-core box it reads ~1400%. Counted as a consumer it inverts the
# whole picture — the idle process ranks as the busiest thing on the machine.
# Matched by name, never by PID 0: on macOS PID 0 is `kernel_task`, which is
# real work and must keep being measured.
_IDLE_PROCESS_NAMES = {"system idle process"}


def _is_idle_placeholder(name: str) -> bool:
    return (name or "").strip().lower() in _IDLE_PROCESS_NAMES


def _trim_cmdline(parts: Optional[List[str]]) -> str:
    if not parts:
        return ""
    joined = " ".join(parts)
    return joined if len(joined) <= _MAX_CMDLINE else joined[: _MAX_CMDLINE - 1] + "…"


def scan_processes() -> List[Dict[str, Any]]:
    """Full per-process inventory for one tick.

    ``cpu_percent()`` is read without an interval — psutil returns usage since
    *this process object's* previous read, and the sampler primes the cache
    once before its first real tick (see :func:`prime_cpu_percent`), so the
    numbers are real rather than the 0.0 a cold first call returns.

    ``exe`` stands in when ``cmdline`` comes back empty. That is not a rare
    edge: on macOS, reading another user's command line needs privileges the
    hub does not have, so every root/``_service`` daemon reports an empty
    cmdline — 310 of 673 processes on the Mac Mini. ``exe`` uses a different
    kernel call (``proc_pidpath``) that stays readable, and it resolved 308 of
    those 310. Without this the path rules would have nothing to match and 42%
    of the machine would stay unattributed regardless of the rule table (#320).

    Every per-process read is individually guarded: a process that exits
    mid-iteration is normal on a busy box and must never abort a capture."""
    out: List[Dict[str, Any]] = []
    fields = ["pid", "ppid", "name", "cmdline", "exe", "cpu_percent", "memory_info",
              "num_threads", "status", "create_time"]
    for proc in psutil.process_iter(fields, ad_value=None):
        try:
            info = proc.info
            name = info.get("name") or ""
            if _is_idle_placeholder(name):
                continue
            cmdline = _trim_cmdline(info.get("cmdline")) or (info.get("exe") or "")
            mem = info.get("memory_info")
            out.append({
                "pid": info.get("pid"),
                "ppid": info.get("ppid"),
                "name": name,
                "cmdline": cmdline,
                "app_id": attribute(name, cmdline),
                "cpu_percent": info.get("cpu_percent"),
                "rss_bytes": getattr(mem, "rss", None) if mem else None,
                "num_threads": info.get("num_threads"),
                "status": info.get("status"),
                "create_time": info.get("create_time"),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception as exc:  # noqa: BLE001 — one bad process never kills a run
            logger.debug("process scan skipped a row: %s", exc)
            continue
    return out


def prime_cpu_percent() -> None:
    """Prime psutil's CPU caches so the first real tick reports actual usage
    instead of 0.0.

    Both levels need priming: ``cpu_percent`` is measured *since the previous
    call*, so a cold system-wide call returns 0.0 just like a cold per-process
    one — which is what made a one-shot capture report 0% CPU on a busy box."""
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


# ---------------------------------------------------------------- port scan


def scan_listening_ports(
    processes: Optional[List[Dict[str, Any]]] = None,
) -> "tuple[List[Dict[str, Any]], bool]":
    """Listening sockets joined to their owning process and app.

    Answers "what is this box actually exposing, and who owns each port" —
    immediately meaningful on a fleet with a documented port map (8000, 808x,
    809x…). Needs elevated privileges on macOS to see other users' sockets.

    Returns ``(rows, denied)``. On ``AccessDenied`` it still degrades to an
    empty list rather than failing the run, but reports ``denied=True`` so the
    caller can record coverage — an empty list and a *blind* list are otherwise
    indistinguishable once stored, and treating "couldn't look" as "nothing
    there" is exactly the defect #322 fixes."""
    by_pid = {p.get("pid"): p for p in (processes or [])}
    out: List[Dict[str, Any]] = []
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError) as exc:
        logger.debug("net_connections denied: %s", exc)
        return [], True
    except Exception as exc:  # noqa: BLE001
        logger.debug("net_connections failed: %s", exc)
        return [], False

    seen: set[tuple] = set()
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
        name = owner.get("name") or _name_for_pid(conn.pid)
        out.append({
            "port": port,
            "proto": proto,
            "address": getattr(conn.laddr, "ip", "") or "",
            "pid": conn.pid,
            "name": name,
            "app_id": owner.get("app_id") or attribute(name, ""),
        })
    return out, False


def _name_for_pid(pid: Optional[int]) -> str:
    if not pid:
        return ""
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""
    except Exception:  # noqa: BLE001
        return ""
