"""Entry point for ``python -m tray`` (called by ``tray.bat`` via pythonw.exe).

pythonw has no console, so an unhandled exception would otherwise vanish
silently. We don't want a perpetual log file though — routine logs are
discarded; only an actual crash writes a one-shot ``tray-crash.log`` next
to the repo root with the traceback. Delete it any time; we'll only
recreate it on the next crash.
"""

from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CRASH_FILE = PROJECT_ROOT / "tray-crash.log"


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(logging.NullHandler())


def _write_crash(exc: BaseException) -> None:
    try:
        with CRASH_FILE.open("w", encoding="utf-8") as fp:
            fp.write(f"# tray crash at {datetime.now().isoformat(timespec='seconds')}\n\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=fp)
    except OSError:
        pass


def main() -> int:
    _setup_logging()
    try:
        from .single_instance import SingleInstance

        # In-process named-mutex single-instance (project-scaffolding#39),
        # replacing the former PID-file lock. Held for the tray's lifetime
        # (tray_main blocks); the OS frees the mutex on exit.
        instance = SingleInstance(r"Global\local-llm-hub-tray")
        if not instance.acquired:
            return 0
        from .tray import main as tray_main

        return tray_main()
    except Exception as exc:  # noqa: BLE001
        _write_crash(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
