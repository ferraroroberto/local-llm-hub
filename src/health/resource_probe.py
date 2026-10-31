"""One resource sample: free RAM, free space per fixed drive, latency per disk.

Everything here is a counter or a metadata read, so a sample costs well under
a millisecond and never reads the disks it measures: ``psutil.disk_usage`` is a
volume-metadata query, and latency comes from the Windows performance counters
(``disk_counters``), the same per-disk averages ``Get-Counter`` shows. Each
sample closes one window: the average time one operation took since the last
sample, which is what a stalled disk inflates (a queue of 13 requests behind a
1.7 s read shows up as 1.7 s).

A reading that could not be taken is its own state (``unknown``), never a
value: a missing disk-number mapping, a failed counter query, the first sample
with no window yet, or a window whose numbers are not credible must not read as
"0 ms, fine". Latency is ``unsupported`` off Windows, where a mount point has
no cheap mapping onto per-disk counters; the free-space and RAM reads work
everywhere.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, Callable, Dict, List, Optional

import psutil

from . import win_probe
from .disk_counters import DiskCounters
from .resource_rules import LATENCY_SOURCE

logger = logging.getLogger(__name__)

_GB = 1024 ** 3
_NON_DATA_MOUNT_PREFIXES = ("/System/Volumes", "/boot", "/snap")
# A disk that completed this many operations in a window spent measurable time
# on them; an average of exactly zero means the source is not measuring.
_CREDIBLE_MIN_IOS = 50

Window = Dict[int, Dict[str, float]]


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


def direction(avg_s: float, ios: float, kind: str) -> Dict[str, Any]:
    """``{ios, ms}`` for one direction of a window.

    ``ms`` is ``None`` when no operation completed (nothing to average). Raises
    ``ValueError`` when the numbers cannot be true: a negative value, or many
    operations that took no time at all."""
    count = int(round(ios))
    if count < 0 or avg_s < 0:
        raise ValueError(f"negative {kind} counter")
    if count >= _CREDIBLE_MIN_IOS and avg_s == 0:
        raise ValueError(f"source not credible: {count} {kind}s took 0 ms in total")
    return {"ios": count, "ms": round(avg_s * 1000.0, 3) if count else None}


def _open_counters() -> Optional[Any]:
    """The live PDH query, or ``None`` when this machine cannot open one."""
    try:
        return DiskCounters()
    except OSError as exc:
        logger.info("ℹ️ resource watch: disk performance counters unavailable: %s", exc)
        return None


class ResourceProbe:
    """Takes samples; keeps the open counter query whose windows give latency."""

    def __init__(self, *, disk_number: Optional[Callable[[str], int]] = None,
                 counters: Optional[Callable[[], Optional[Any]]] = None) -> None:
        self._disk_numbers: Dict[str, int] = {}
        self._disk_number = disk_number or (win_probe.volume_disk_number if win_probe.available() else None)
        self._open = counters or _open_counters
        self._counters: Optional[Any] = None

    def _window(self) -> Optional[Window]:
        """Close the current counter window, or ``None`` when there is no usable one."""
        if self._disk_number is None:
            return None
        if self._counters is None:
            self._counters = self._open()
            if self._counters is None:
                return None
        try:
            return self._counters.collect()
        except OSError as exc:
            logger.info("ℹ️ resource watch: disk counter read failed: %s", exc)
            return None

    def prime(self) -> None:
        """Open the first window so the first real sample already has one."""
        self._window()

    def _disk_of(self, label: str) -> Optional[int]:
        """The physical disk number holding this drive, or ``None``."""
        if label in self._disk_numbers:
            return self._disk_numbers[label]
        if self._disk_number is None:
            return None
        try:
            number = int(self._disk_number(label))
        except OSError as exc:
            logger.info("ℹ️ resource watch: no physical disk for %s: %s", label, exc)
            return None
        self._disk_numbers[label] = number
        return number

    def _latency(self, label: str, window: Optional[Window]) -> Dict[str, Any]:
        if self._disk_number is None:
            return {"lat": "unsupported", "disk": None}
        number = self._disk_of(label)
        disk = f"PhysicalDrive{number}" if number is not None else None
        if number is None or window is None or number not in window:
            return {"lat": "unknown", "disk": disk}
        values = window[number]
        try:
            read = direction(values["read_s"], values["reads"], "read")
            write = direction(values["write_s"], values["writes"], "write")
        except ValueError as exc:
            logger.info("ℹ️ resource watch: %s latency window dropped: %s", label, exc)
            return {"lat": "unknown", "disk": disk, "why": str(exc)}
        return {"lat": "ok", "src": LATENCY_SOURCE, "disk": disk, "read_ms": read["ms"], "write_ms": write["ms"],
                "read_ios": read["ios"], "write_ios": write["ios"]}

    def _drive(self, mount: str, window: Optional[Window]) -> Dict[str, Any]:
        row: Dict[str, Any] = self._latency(drive_label(mount), window)
        try:
            usage = psutil.disk_usage(mount)
            row.update(free="ok", free_gb=round(usage.free / _GB, 1), total_gb=round(usage.total / _GB, 1))
        except OSError as exc:
            logger.info("ℹ️ resource watch: free space of %s unreadable: %s", mount, exc)
            row.update(free="unknown")
        return row

    def sample(self) -> Dict[str, Any]:
        """Probe this machine once. Cheap; safe on any thread, one call at a time."""
        started = time.perf_counter()
        ram: Dict[str, Any]
        try:
            vm = psutil.virtual_memory()
            ram = {"state": "ok", "free_percent": round(100.0 * vm.available / vm.total, 1),
                   "free_gb": round(vm.available / _GB, 1)}
        except OSError as exc:
            logger.info("ℹ️ resource watch: memory probe failed: %s", exc)
            ram = {"state": "unknown"}
        window = self._window()
        drives: Dict[str, Dict[str, Any]] = {}
        drives_state = "ok"
        try:
            for mount in fixed_mounts():
                drives[drive_label(mount)] = self._drive(mount, window)
        except OSError as exc:
            logger.info("ℹ️ resource watch: drive list failed: %s", exc)
            drives_state = "unknown"
        return {"ts": time.time(), "ram": ram, "drives": drives, "drives_state": drives_state,
                "sample_ms": round((time.perf_counter() - started) * 1000, 2)}
