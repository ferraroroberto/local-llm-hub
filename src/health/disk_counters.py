"""Per-disk latency from the Windows performance counters (PDH).

The same ``\\PhysicalDisk(*)`` counters ``Get-Counter`` and Resource Monitor
show: ``Avg. Disk sec/Read`` / ``Avg. Disk sec/Write`` (the average time one
operation took between two collections) and ``Disk Reads/sec`` /
``Disk Writes/sec`` (how many completed). psutil's per-disk ``read_time`` /
``write_time`` are not used: on Windows they come back in whole seconds while
labelled milliseconds, so every disk reads about 1000 times too fast and a
busy NVMe truncates to zero.

A collection is a counter read from the kernel's performance data, not a disk
I/O, so it never touches the disks it measures. The query stays open between
samples; each ``collect()`` closes one window and opens the next.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = 0x800007D2
# Per-instance statuses that carry a usable value (valid, or valid and new).
_GOOD_CSTATUS = (0x0, 0x1)
_COUNTERS = {
    "read_s": "\\PhysicalDisk(*)\\Avg. Disk sec/Read",
    "write_s": "\\PhysicalDisk(*)\\Avg. Disk sec/Write",
    "reads_per_s": "\\PhysicalDisk(*)\\Disk Reads/sec",
    "writes_per_s": "\\PhysicalDisk(*)\\Disk Writes/sec",
}


class _FmtValue(ctypes.Structure):
    # PDH_FMT_COUNTERVALUE with the union read as its double member.
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


def _check(status: int, what: str) -> None:
    status &= 0xFFFFFFFF
    if status:
        raise OSError(f"{what} failed: PDH status 0x{status:08X}")


def _disk_number(instance: str) -> Optional[int]:
    """``"1 E:"`` -> 1; ``"_Total"`` and anything without a leading number -> ``None``."""
    head = instance.split(" ", 1)[0]
    return int(head) if head.isdigit() else None


class DiskCounters:
    """An open PDH query over every physical disk. Not safe for concurrent
    calls; the resource watch calls it from one tick at a time."""

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh")
        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self._pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self._pdh.PdhGetFormattedCounterArrayW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self._pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        self._query = wintypes.HANDLE()
        _check(self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)), "PdhOpenQuery")
        self._counters: Dict[str, wintypes.HANDLE] = {}
        try:
            for key, path in _COUNTERS.items():
                handle = wintypes.HANDLE()
                # The English name works on any display language (this box formats "0,011").
                _check(self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle)),
                       f"PdhAddEnglishCounter {path}")
                self._counters[key] = handle
        except OSError:
            self.close()
            raise
        self._last: Optional[float] = None

    def close(self) -> None:
        if self._query:
            self._pdh.PdhCloseQuery(self._query)
            self._query = wintypes.HANDLE()

    def _values(self, counter: wintypes.HANDLE) -> Dict[int, float]:
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None) & 0xFFFFFFFF
        if status != _PDH_MORE_DATA:
            _check(status, "PdhGetFormattedCounterArray (size)")
            return {}
        buf = ctypes.create_string_buffer(size.value)
        _check(self._pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf),
            "PdhGetFormattedCounterArray")
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        out: Dict[int, float] = {}
        for i in range(count.value):
            number = _disk_number(items[i].szName or "")
            if number is not None and items[i].FmtValue.CStatus in _GOOD_CSTATUS:
                out[number] = float(items[i].FmtValue.doubleValue)
        return out

    def collect(self) -> Optional[Dict[int, Dict[str, float]]]:
        """Close the current window: ``{disk_number: {read_s, write_s, reads, writes}}``.

        ``read_s`` / ``write_s`` are the average seconds per operation over the
        window; ``reads`` / ``writes`` the operations completed in it. ``None``
        on the first call, which only opens a window. A disk missing from the
        result had no usable value. Raises ``OSError`` when PDH fails."""
        _check(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")
        now, last = time.monotonic(), self._last
        self._last = now
        if last is None:
            return None
        elapsed = now - last
        values = {key: self._values(handle) for key, handle in self._counters.items()}
        out: Dict[int, Dict[str, float]] = {}
        for number in set(values["read_s"]) & set(values["write_s"]) & set(values["reads_per_s"]) & set(values["writes_per_s"]):
            out[number] = {
                "read_s": values["read_s"][number], "write_s": values["write_s"][number],
                "reads": values["reads_per_s"][number] * elapsed, "writes": values["writes_per_s"][number] * elapsed,
            }
        return out
