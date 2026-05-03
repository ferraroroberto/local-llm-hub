"""PID-file lock so a second ``tray.bat`` invocation exits instead of stacking icons.

Validates the recorded PID against :mod:`psutil` so a stale lock from a
crashed previous run doesn't permanently block launches.
"""

from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

LOCK_FILE = Path(__file__).resolve().parent.parent / ".tray.pid"


def _is_live_tray_process(pid: int) -> bool:
    """True iff *pid* is alive and looks like another tray instance."""
    if not psutil.pid_exists(pid):
        return False
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline()).lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    # Match either the package form (`-m tray`) or the tray.bat path.
    return "tray" in cmdline and ("python" in cmdline or "tray.bat" in cmdline)


def acquire_lock() -> bool:
    """Return True if we got the lock, False if another tray is already running."""
    if LOCK_FILE.exists():
        try:
            existing_pid = int(LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            existing_pid = 0
        if existing_pid and _is_live_tray_process(existing_pid):
            logger.warning("⚠️  tray already running (pid %s) — exiting", existing_pid)
            return False

    try:
        LOCK_FILE.write_text(str(os.getpid()))
    except OSError as exc:
        logger.warning("⚠️  could not write lock file %s: %s", LOCK_FILE, exc)
        return True  # don't block startup just because we couldn't lock

    atexit.register(release_lock)
    return True


def release_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass
