"""The storage section of a capture: drives, big folders, known growers, Docker.

Read-only and bounded. Every field is either established within its bound or
reported ``{"status": "unknown", "reason": ...}``; a number that a time, depth,
entry or permission limit cut short is never returned as a size, only as a
``lower_bound_bytes`` beside the reason. One field going unknown never stops
the rest of the section — a missing Docker engine or a dead network drive must
not cost the capture its other answers.

Blocking filesystem and subprocess calls run in daemon worker threads with an
outer timeout: a ``scandir`` wedged on a hung mount cannot be interrupted, so
the caller abandons the thread and reports the field unknown instead of hanging
the capture with it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

from ..atomic_write import read_json
from ..no_window import NO_WINDOW

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config" / "diagnostics_storage.local.json"

STORAGE_SCHEMA = 1

# How long past its own budget a walk may run before its thread is abandoned.
_ABANDON_GRACE_S = 2.0
_DRIVE_USAGE_TIMEOUT_S = 3.0
_DRIVE_LIST_TIMEOUT_S = 5.0
_MAX_CONTAINERS = 200
# Directory entries between two clock reads; keeps the walk's own overhead low.
_CLOCK_EVERY = 256
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

Clock = Callable[[], float]


# ------------------------------------------------------------------- config


@dataclass(frozen=True)
class StorageConfig:
    """Where to look and how long to spend. Paths come from the environment or
    a gitignored local file, never from this module's source."""

    roots: Tuple[Path, ...]
    growers: Tuple[Tuple[str, Path], ...]
    walk_budget_s: float = 8.0
    overall_budget_s: float = 30.0
    max_depth: int = 8
    max_entries: int = 200_000
    max_children: int = 60
    top_n: int = 10
    docker_timeout_s: float = 8.0


def _default_config() -> StorageConfig:
    growers: List[Tuple[str, Path]] = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        growers.append(("docker-desktop-wsl", Path(local_appdata) / "Docker" / "wsl"))
    hf_home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    growers.append(("huggingface-cache", Path(hf_home)))
    growers.append(("hub-models", PROJECT_ROOT / "models"))
    return StorageConfig(roots=(Path.home(),), growers=tuple(growers))


def _expand(raw: Any) -> Optional[Path]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(os.path.expandvars(os.path.expanduser(raw.strip())))


def _number(data: Dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(data.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def load_config(path: Optional[Path] = None) -> StorageConfig:
    """Defaults, overridden by the local file when one exists.

    ``roots`` replaces the default roots; ``growers`` adds to the default
    growers by label (a label that already exists is replaced). A missing or
    malformed file yields the defaults — a broken config never stops a capture."""
    cfg = _default_config()
    data = read_json(path or LOCAL_CONFIG_PATH, "diagnostics storage config")
    if not isinstance(data, dict):
        return cfg

    roots = tuple(p for p in (_expand(r) for r in data.get("roots") or []) if p)
    growers = dict(cfg.growers)
    for item in data.get("growers") or []:
        if not isinstance(item, dict):
            continue
        label, target = item.get("label"), _expand(item.get("path"))
        if isinstance(label, str) and label.strip() and target:
            growers[label.strip()] = target
    return replace(
        cfg,
        roots=roots if "roots" in data else cfg.roots,
        growers=tuple(growers.items()),
        walk_budget_s=_number(data, "walk_budget_s", cfg.walk_budget_s, 0.5, 60),
        overall_budget_s=_number(data, "overall_budget_s", cfg.overall_budget_s, 1, 300),
        max_depth=int(_number(data, "max_depth", cfg.max_depth, 1, 32)),
        max_entries=int(_number(data, "max_entries", cfg.max_entries, 1_000, 5_000_000)),
        max_children=int(_number(data, "max_children", cfg.max_children, 1, 500)),
        top_n=int(_number(data, "top_n", cfg.top_n, 1, 50)),
        docker_timeout_s=_number(data, "docker_timeout_s", cfg.docker_timeout_s, 1, 60),
    )


# ------------------------------------------------------------------ helpers


def unknown(reason: str, **extra: Any) -> Dict[str, Any]:
    """The one shape for a field that could not be established."""
    return {"status": "unknown", "reason": reason, **extra}


class _Deadline:
    """The capture-wide time budget, read through an injectable clock."""

    def __init__(self, budget_s: float, clock: Clock) -> None:
        self._clock = clock
        self._end = clock() + budget_s

    def remaining(self) -> float:
        return self._end - self._clock()


def _run_bounded(fn: Callable[[threading.Event], Any], timeout_s: float) -> Tuple[bool, Any]:
    """Run ``fn(cancel)`` in a daemon thread; ``(False, None)`` on timeout.

    A daemon thread, not an executor worker: a call wedged in the kernel would
    otherwise be joined at interpreter exit. ``cancel`` is set on abandonment so
    a thread that later wakes stops instead of walking on."""
    cancel = threading.Event()
    box: Dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn(cancel)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=target, name="diag-storage", daemon=True)
    worker.start()
    worker.join(max(0.0, timeout_s))
    if worker.is_alive():
        cancel.set()
        return False, None
    if "error" in box:
        raise box["error"]
    return True, box.get("value")


def _is_dir_link(entry: os.DirEntry) -> bool:
    """A symlink, junction or other reparse-point directory — never descended,
    so a link loop or a mount point cannot inflate or stall a walk."""
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & _REPARSE_POINT)
    except OSError:
        return True


# --------------------------------------------------------------- size walks


def bounded_size(
    path: Path,
    *,
    budget_s: float,
    max_depth: int,
    max_entries: int,
    clock: Clock = time.monotonic,
    cancel: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """Total bytes under ``path``, or ``unknown`` with the lower bound counted.

    Never follows links. A file's own size is returned directly. Any bound hit
    (time, depth, entry count) or any unreadable directory makes the answer a
    lower bound, so the result is ``unknown`` rather than a short number."""
    try:
        top = os.lstat(path)
    except FileNotFoundError:
        return {"status": "absent"}
    except OSError as exc:
        return unknown(f"{type(exc).__name__}: {exc.strerror or exc}")
    if not stat.S_ISDIR(top.st_mode):
        return {"status": "ok", "bytes": int(top.st_size)}

    end = clock() + budget_s
    total = entries = unreadable = 0
    reasons: List[str] = []
    stack: List[Tuple[str, int]] = [(str(path), 0)]
    while stack:
        current, depth = stack.pop()
        if cancel is not None and cancel.is_set():
            reasons.append("walk abandoned")
            break
        if clock() >= end:
            reasons.append(f"time budget {budget_s:.0f}s reached")
            break
        try:
            with os.scandir(current) as it:
                for entry in it:
                    entries += 1
                    if entries > max_entries:
                        reasons.append(f"entry cap {max_entries} reached")
                        stack.clear()
                        break
                    if entries % _CLOCK_EVERY == 0 and clock() >= end:
                        reasons.append(f"time budget {budget_s:.0f}s reached")
                        stack.clear()
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if _is_dir_link(entry):
                                continue
                            if depth + 1 > max_depth:
                                reasons.append(f"depth cap {max_depth} reached")
                                continue
                            stack.append((entry.path, depth + 1))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        unreadable += 1
        except OSError:
            unreadable += 1
    if unreadable:
        reasons.append(f"{unreadable} unreadable entr{'y' if unreadable == 1 else 'ies'}")
    if reasons:
        return unknown("; ".join(dict.fromkeys(reasons)), lower_bound_bytes=total)
    return {"status": "ok", "bytes": total}


def _measure(path: Path, cfg: StorageConfig, deadline: _Deadline, clock: Clock) -> Dict[str, Any]:
    """One path's size under both the per-walk and the capture-wide budget."""
    budget = min(cfg.walk_budget_s, deadline.remaining())
    if budget <= 0:
        return unknown("capture time budget exhausted")

    def walk(cancel: threading.Event) -> Dict[str, Any]:
        return bounded_size(path, budget_s=budget, max_depth=cfg.max_depth,
                            max_entries=cfg.max_entries, clock=clock, cancel=cancel)

    finished, result = _run_bounded(walk, budget + _ABANDON_GRACE_S)
    return result if finished else unknown(f"walk did not return within {budget:.0f}s")


# ---------------------------------------------------------------- sections


def _drives(deadline: _Deadline) -> Dict[str, Any]:
    finished, parts = _run_bounded(lambda _c: psutil.disk_partitions(all=False),
                                   _DRIVE_LIST_TIMEOUT_S)
    if not finished:
        return unknown("listing drives timed out")
    items: List[Dict[str, Any]] = []
    for part in parts:
        base = {"mount": part.mountpoint, "fstype": part.fstype}
        timeout = min(_DRIVE_USAGE_TIMEOUT_S, deadline.remaining())
        if timeout <= 0:
            items.append({**base, **unknown("capture time budget exhausted")})
            continue
        try:
            ok, usage = _run_bounded(lambda _c, p=part.mountpoint: psutil.disk_usage(p), timeout)
        except OSError as exc:
            items.append({**base, **unknown(f"{type(exc).__name__}: {exc.strerror or exc}")})
            continue
        if not ok:
            items.append({**base, **unknown(f"usage did not return within {timeout:.0f}s")})
            continue
        items.append({**base, "status": "ok", "total_bytes": int(usage.total),
                      "free_bytes": int(usage.free)})
    return {"status": "ok", "items": items}


def _growers(cfg: StorageConfig, deadline: _Deadline, clock: Clock) -> Dict[str, Any]:
    items = [{"label": label, "path": str(path), **_measure(path, cfg, deadline, clock)}
             for label, path in cfg.growers]
    return {"status": "ok", "items": items}


def _list_children(root: Path, limit: int) -> Dict[str, Any]:
    """The root's immediate real directories, up to ``limit``."""
    def scan(_cancel: threading.Event) -> Tuple[List[Path], bool]:
        found: List[Path] = []
        with os.scandir(root) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False) and not _is_dir_link(entry):
                    if len(found) >= limit:
                        return found, True
                    found.append(Path(entry.path))
        return found, False

    try:
        finished, value = _run_bounded(scan, _DRIVE_LIST_TIMEOUT_S)
    except OSError as exc:
        return unknown(f"{type(exc).__name__}: {exc.strerror or exc}")
    if not finished:
        return unknown("listing folders timed out")
    children, truncated = value
    return {"status": "ok", "children": children, "truncated": truncated}


def _top_folders(cfg: StorageConfig, deadline: _Deadline, clock: Clock) -> Dict[str, Any]:
    roots: List[Dict[str, Any]] = []
    for root in cfg.roots:
        listing = _list_children(root, cfg.max_children)
        if listing["status"] != "ok":
            roots.append({"root": str(root), **listing})
            continue
        sized = [{"path": str(child), **_measure(child, cfg, deadline, clock)}
                 for child in listing["children"]]
        # Rank by the best number known; an unknown one ranks on its lower
        # bound and keeps its status, so it can never pass for a size.
        sized.sort(key=lambda f: f.get("bytes", f.get("lower_bound_bytes", 0)), reverse=True)
        roots.append({
            "root": str(root), "status": "ok", "folders": sized[:cfg.top_n],
            "measured": sum(1 for f in sized if f["status"] == "ok"),
            "unmeasured": sum(1 for f in sized if f["status"] != "ok"),
            "children_truncated": listing["truncated"],
        })
    return {"status": "ok", "roots": roots}


# ------------------------------------------------------------------- docker


def _docker(args: List[str], timeout_s: float) -> Tuple[bool, str]:
    """Run one docker CLI call; ``(False, reason)`` when it did not succeed."""
    try:
        proc = subprocess.run(
            ["docker", *args], capture_output=True, timeout=timeout_s,
            creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False, f"`docker {args[0]}` timed out after {timeout_s:.0f}s"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc.strerror or exc}"
    if proc.returncode != 0:
        lines = (proc.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        return False, (lines[0] if lines else f"exit {proc.returncode}")[:200]
    return True, (proc.stdout or b"").decode("utf-8", errors="replace")


def _json_lines(text: str) -> List[Dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


_HEALTH = re.compile(r"\((healthy|unhealthy|health: starting)\)")


def _containers(timeout_s: float) -> Dict[str, Any]:
    ok, out = _docker(["ps", "-a", "--format", "{{json .}}"], timeout_s)
    if not ok:
        return unknown(out)
    rows = _json_lines(out)
    items = []
    for row in rows[:_MAX_CONTAINERS]:
        match = _HEALTH.search(str(row.get("Status", "")))
        items.append({"name": row.get("Names"), "state": row.get("State"),
                      "health": match.group(1) if match else None})
    return {"status": "ok", "items": items, "truncated": len(rows) > _MAX_CONTAINERS}


def _docker_usage(timeout_s: float) -> Dict[str, Any]:
    """Image, container, volume and build-cache totals, as docker prints them
    (human sizes), so no rounding of ours is mistaken for a measurement."""
    ok, out = _docker(["system", "df", "--format", "{{json .}}"], timeout_s)
    if not ok:
        return unknown(out)
    items = [{"type": r.get("Type"), "total_count": r.get("TotalCount"),
              "active": r.get("Active"), "size": r.get("Size"),
              "reclaimable": r.get("Reclaimable")} for r in _json_lines(out)]
    return {"status": "ok", "items": items}


def _docker_state(cfg: StorageConfig, deadline: _Deadline) -> Dict[str, Any]:
    if shutil.which("docker") is None:
        return unknown("docker CLI not on PATH")

    def budget() -> float:
        return min(cfg.docker_timeout_s, deadline.remaining())

    if budget() <= 0:
        return unknown("capture time budget exhausted")
    ok, out = _docker(["info", "--format", "{{.ServerVersion}}"], budget())
    if not ok:
        return unknown(out)
    state: Dict[str, Any] = {"status": "ok", "server_version": out.strip()}
    for key, fetch in (("containers", _containers), ("usage", _docker_usage)):
        timeout = budget()
        state[key] = fetch(timeout) if timeout > 0 else unknown("capture time budget exhausted")
    return state


# ------------------------------------------------------------------ collect


def _guarded(name: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """One section failing for any reason leaves the others intact."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - a section must never fail the capture
        logger.warning("⚠️ diagnostics storage section %s failed: %s", name, exc)
        return unknown(f"{type(exc).__name__}: {exc}")


def collect(cfg: Optional[StorageConfig] = None, *, clock: Clock = time.monotonic) -> Dict[str, Any]:
    """Build the whole storage section. Blocking; call from a worker thread."""
    cfg = cfg or load_config()
    started = clock()
    deadline = _Deadline(cfg.overall_budget_s, clock)
    section = {
        "drives": _guarded("drives", lambda: _drives(deadline)),
        "docker": _guarded("docker", lambda: _docker_state(cfg, deadline)),
        "growers": _guarded("growers", lambda: _growers(cfg, deadline, clock)),
        "top_folders": _guarded("top_folders", lambda: _top_folders(cfg, deadline, clock)),
    }
    return {"schema": STORAGE_SCHEMA, "collected_at": time.time(),
            "elapsed_s": round(clock() - started, 1), **section}
