"""One resource sample: free RAM, free space per fixed drive, latency per disk.

Everything here is a counter or a metadata read, so a sample costs well under
a millisecond and never reads the disks it measures: ``psutil.disk_usage`` is a
volume-metadata query, and latency comes from the per-disk I/O counters the
storage stack already keeps (cumulative operation counts and the time they
took). Latency is the *delta* between two samples: ``Δtime / Δcount`` is the
average time one operation took in that window, which is what a stalled disk
inflates (a queue of 13 requests behind a 1.7 s read shows up as 1.7 s).

A reading that could not be taken is its own state (``unknown``), never a
value: a missing disk-number mapping, a counter that went backwards (reset) or
a failed ``psutil`` call must not read as "0 ms, fine". Latency is
``unsupported`` off Windows, where a mount point has no cheap mapping onto the
disk counters; the free-space and RAM reads work everywhere.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, Callable, Dict, List, Optional

import psutil

from . import win_probe

logger = logging.getLogger(__name__)

_GB = 1024 ** 3
_NON_DATA_MOUNT_PREFIXES = ("/System/Volumes", "/boot", "/snap")


def fixed_mounts() -> List[str]:
    """Mount points of the fixed drives: ``C:\\`` style on Windows, real mounts elsewhere."""
    out = []
    for part in psutil.disk_partitions(all=False):
        if sys.platform == "win32":
            if "fixed" not in part.opts.split(","):
                continue
        elif part.device.startswith("/dev/loop") or part.mountpoint.startswith(_NON_DATA_MOUNT_PREFIXES):
            continue
        out.append(part.mountpoint)
    return out


def drive_label(mount: str) -> str:
    """``E:\\`` -> ``E:``; a POSIX mount point is its own label."""
    return mount[:2] if sys.platform == "win32" else mount


def _delta(prev: Any, cur: Any, kind: str) -> Dict[str, Any]:
    """``{ios, ms}`` for one direction between two counter snapshots.

    ``ms`` is ``None`` when no operation completed in the window (nothing to
    average). Raises ``ValueError`` on a counter that went backwards, which
    means the counters were reset and the window cannot be trusted."""
    count = getattr(cur, f"{kind}_count") - getattr(prev, f"{kind}_count")
    spent = getattr(cur, f"{kind}_time") - getattr(prev, f"{kind}_time")
    if count < 0 or spent < 0:
        raise ValueError(f"{kind} counters went backwards")
    return {"ios": count, "ms": round(spent / count, 1) if count else None}


class ResourceProbe:
    """Takes samples; keeps the previous I/O counters to turn them into deltas."""

    def __init__(self, *, disk_number: Optional[Callable[[str], int]] = None) -> None:
        self._prev_io: Dict[str, Any] = {}
        self._disk_numbers: Dict[str, str] = {}
        self._disk_number = disk_number or (win_probe.volume_disk_number if win_probe.available() else None)

    def _read_io(self) -> Optional[Dict[str, Any]]:
        try:
            counters = psutil.disk_io_counters(perdisk=True)
        except (OSError, RuntimeError) as exc:
            logger.info("ℹ️ resource watch: disk I/O counters failed: %s", exc)
            return None
        return counters or None

    def prime(self) -> None:
        """Take the baseline counters so the first real sample already has a delta."""
        io = self._read_io()
        if io:
            self._prev_io = io

    def _disk_of(self, label: str) -> Optional[str]:
        """The ``PhysicalDriveN`` psutil keys this drive's counters by, or ``None``."""
        if label in self._disk_numbers:
            return self._disk_numbers[label]
        if self._disk_number is None:
            return None
        try:
            name = f"PhysicalDrive{self._disk_number(label)}"
        except OSError as exc:
            logger.info("ℹ️ resource watch: no physical disk for %s: %s", label, exc)
            return None
        self._disk_numbers[label] = name
        return name

    def _latency(self, label: str, io: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if self._disk_number is None:
            return {"lat": "unsupported", "disk": None}
        disk = self._disk_of(label)
        if disk is None or io is None or disk not in io or disk not in self._prev_io:
            return {"lat": "unknown", "disk": disk}
        try:
            read = _delta(self._prev_io[disk], io[disk], "read")
            write = _delta(self._prev_io[disk], io[disk], "write")
        except ValueError as exc:
            logger.info("ℹ️ resource watch: %s latency window dropped: %s", label, exc)
            return {"lat": "unknown", "disk": disk}
        return {"lat": "ok", "disk": disk, "read_ms": read["ms"], "write_ms": write["ms"],
                "read_ios": read["ios"], "write_ios": write["ios"]}

    def _drive(self, mount: str, io: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        row: Dict[str, Any] = self._latency(drive_label(mount), io)
        try:
            usage = psutil.disk_usage(mount)
            row.update(free="ok", free_gb=round(usage.free / _GB, 1), total_gb=round(usage.total / _GB, 1))
        except OSError as exc:
            logger.info("ℹ️ resource watch: free space of %s unreadable: %s", mount, exc)
            row.update(free="unknown")
        return row

    def sample(self) -> Dict[str, Any]:
        """Probe this machine once. Cheap; safe on any thread."""
        started = time.perf_counter()
        ram: Dict[str, Any]
        try:
            vm = psutil.virtual_memory()
            ram = {"state": "ok", "free_percent": round(100.0 * vm.available / vm.total, 1),
                   "free_gb": round(vm.available / _GB, 1)}
        except OSError as exc:
            logger.info("ℹ️ resource watch: memory probe failed: %s", exc)
            ram = {"state": "unknown"}
        io = self._read_io()
        drives: Dict[str, Dict[str, Any]] = {}
        drives_state = "ok"
        try:
            for mount in fixed_mounts():
                drives[drive_label(mount)] = self._drive(mount, io)
        except OSError as exc:
            logger.info("ℹ️ resource watch: drive list failed: %s", exc)
            drives_state = "unknown"
        if io:
            self._prev_io = io
        return {"ts": time.time(), "ram": ram, "drives": drives, "drives_state": drives_state,
                "sample_ms": round((time.perf_counter() - started) * 1000, 2)}
