"""Native Windows probes for the health watch.

psutil reads handle, thread and private-byte counts one process at a time,
opening each process: about 8 s for ~600 processes on the tower. The kernel
hands back the same numbers for every process in one
``NtQuerySystemInformation(SystemProcessInformation)`` call (~15 ms), and one
``GetPerformanceInfo`` call covers commit, pools and the system handle total.
Every function raises ``OSError`` on failure so the sampler can record the
probe as failed rather than read a gap as zero.
"""

from __future__ import annotations

import ctypes
import logging
import re
import struct
import subprocess
import sys
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

from ..no_window import NO_WINDOW

logger = logging.getLogger(__name__)

_SYSTEM_PROCESS_INFORMATION = 5
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GR_GDIOBJECTS = 0
_GR_USEROBJECTS = 1
_IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
_FILE_SHARE_READ_WRITE = 0x3
_OPEN_EXISTING = 3
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Field offsets in the 64-bit SYSTEM_PROCESS_INFORMATION record (winternl.h).
_OFF_THREADS = 4
_OFF_CREATE_TIME = 32
_OFF_IMAGE_NAME = 56
_OFF_PID = 80
_OFF_HANDLES = 96
_OFF_PRIVATE = 200


class _PerformanceInformation(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", wintypes.DWORD),
        ("ProcessCount", wintypes.DWORD),
        ("ThreadCount", wintypes.DWORD),
    ]


def available() -> bool:
    """True on 64-bit Windows, the only layout the offsets above describe."""
    return sys.platform == "win32" and struct.calcsize("P") == 8


def process_table() -> List[Dict[str, object]]:
    """Every process as ``{pid, name, create_time, threads, handles, private_bytes}``.

    ``create_time`` is the raw FILETIME, enough to tell a restarted process
    from the one that held its PID before."""
    ntdll = ctypes.WinDLL("ntdll")
    size = ctypes.c_ulong(0)
    buf = ctypes.create_string_buffer(1 << 20)
    for _ in range(5):
        status = ntdll.NtQuerySystemInformation(
            _SYSTEM_PROCESS_INFORMATION, buf, len(buf), ctypes.byref(size)
        ) & 0xFFFFFFFF
        if status != _STATUS_INFO_LENGTH_MISMATCH:
            break
        # Processes come and go between calls, so leave headroom.
        buf = ctypes.create_string_buffer(size.value + (1 << 16))
    if status:
        raise OSError(f"NtQuerySystemInformation failed: 0x{status:08X}")
    raw = buf.raw
    rows: List[Dict[str, object]] = []
    off = 0
    while True:
        next_off, threads = struct.unpack_from("<II", raw, off)
        length = struct.unpack_from("<H", raw, off + _OFF_IMAGE_NAME)[0]
        name_ptr = struct.unpack_from("<Q", raw, off + _OFF_IMAGE_NAME + 8)[0]
        pid = struct.unpack_from("<Q", raw, off + _OFF_PID)[0]
        rows.append({
            "pid": int(pid),
            "name": ctypes.wstring_at(name_ptr, length // 2) if name_ptr else "System Idle Process",
            "create_time": struct.unpack_from("<q", raw, off + _OFF_CREATE_TIME)[0],
            "threads": int(threads),
            "handles": struct.unpack_from("<I", raw, off + _OFF_HANDLES)[0],
            "private_bytes": struct.unpack_from("<Q", raw, off + _OFF_PRIVATE)[0],
        })
        if not next_off:
            return rows
        off += next_off


def performance_info() -> Dict[str, float]:
    """Commit, physical memory, kernel pools (bytes) and the system handle total."""
    info = _PerformanceInformation()
    info.cb = ctypes.sizeof(info)
    if not ctypes.WinDLL("psapi").GetPerformanceInfo(ctypes.byref(info), info.cb):
        raise OSError("GetPerformanceInfo failed")
    page = info.PageSize
    return {
        "commit_bytes": float(info.CommitTotal * page),
        "commit_limit_bytes": float(info.CommitLimit * page),
        "physical_total_bytes": float(info.PhysicalTotal * page),
        "physical_available_bytes": float(info.PhysicalAvailable * page),
        "paged_pool_bytes": float(info.KernelPaged * page),
        "nonpaged_pool_bytes": float(info.KernelNonpaged * page),
        "handle_count": float(info.HandleCount),
    }


def gui_resources(pids: List[int]) -> Dict[int, Tuple[int, int]]:
    """``{pid: (gdi, user)}`` for every PID that can be opened; others are left out.

    A process this user cannot open (another session's, a protected service)
    is simply absent: its GUI counts are unknown, not zero."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    user32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    out: Dict[int, Tuple[int, int]] = {}
    for pid in pids:
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            out[pid] = (
                int(user32.GetGuiResources(handle, _GR_GDIOBJECTS)),
                int(user32.GetGuiResources(handle, _GR_USEROBJECTS)),
            )
        finally:
            kernel32.CloseHandle(handle)
    return out


class _StorageDeviceNumber(ctypes.Structure):
    _fields_ = [
        ("DeviceType", wintypes.DWORD),
        ("DeviceNumber", wintypes.DWORD),
        ("PartitionNumber", wintypes.DWORD),
    ]


def volume_disk_number(letter: str) -> int:
    """The physical disk number (``PhysicalDriveN``) that holds drive ``letter``.

    One ``IOCTL_STORAGE_GET_DEVICE_NUMBER`` on a zero-access volume handle: a
    metadata query answered by the storage stack, so it never reads the disk.
    Raises ``OSError`` when the volume cannot be queried, or when it spans
    several disks (a dynamic or striped volume has no single device number)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(
        "\\\\.\\" + letter.rstrip(":\\/") + ":", 0, _FILE_SHARE_READ_WRITE, None, _OPEN_EXISTING, 0, None,
    )
    if handle in (None, _INVALID_HANDLE_VALUE):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        number = _StorageDeviceNumber()
        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            handle, _IOCTL_STORAGE_GET_DEVICE_NUMBER, None, 0,
            ctypes.byref(number), ctypes.sizeof(number), ctypes.byref(returned), None,
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(number.DeviceNumber)
    finally:
        kernel32.CloseHandle(handle)


def dynamic_port_range() -> Optional[Tuple[int, int]]:
    """The IPv4 TCP dynamic (ephemeral) port range as ``(start, end)``, or ``None``.

    Read from ``netsh`` because the range is a machine setting the owner may
    have changed from the 49152-65535 default."""
    try:
        out = subprocess.run(
            ["netsh", "int", "ipv4", "show", "dynamicport", "tcp"],
            capture_output=True, text=True, encoding="oem", errors="replace",
            timeout=10, creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("ℹ️ health watch: netsh dynamicport read failed: %s", exc)
        return None
    numbers = [int(n) for n in re.findall(r":\s*(\d+)", out)]
    if len(numbers) < 2 or numbers[1] <= 0:
        return None
    return numbers[0], numbers[0] + numbers[1] - 1
