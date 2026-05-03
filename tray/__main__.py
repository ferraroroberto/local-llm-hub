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
    # Discard routine logs — pythonw has no stdout to print to anyway, and
    # the user explicitly doesn't want a always-on log file. Crashes are
    # captured separately in main()'s except block.
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
        from .single_instance import acquire_lock
        if not acquire_lock():
            return 0
        from .app import TrayApp
        from .config import load as load_config

        return TrayApp(load_config()).run()
    except Exception as exc:
        _write_crash(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
