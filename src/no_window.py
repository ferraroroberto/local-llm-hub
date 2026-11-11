"""One definition of the Windows ``CREATE_NO_WINDOW`` subprocess flag.

Every ``subprocess`` spawn that launches an external console executable
(``git``, ``netstat``, ``taskkill``, ``powershell.exe``, ``tailscale``, a
helper script, ...) must pass ``creationflags=NO_WINDOW`` on Windows. A parent
with no console of its own -- a ``pythonw`` tray, a scheduled task, a daemon,
an agent's captured shell -- otherwise makes Windows allocate a fresh console
per child, flashing a window on screen; on a poll loop that reads as malware
or a stuck app. See ``CLAUDE.md`` -> "Windows console-subprocess suppression
(``CREATE_NO_WINDOW``)".

Import the flag from here instead of re-deriving the
``sys.platform == "win32"`` ternary at each call site::

    from src.no_window import NO_WINDOW

    subprocess.run([...], creationflags=NO_WINDOW)

**Vendor-verbatim modules are the one exception and keep a local definition.**
A file copied byte-identical into adopter repos cannot import a module of this
repo's -- the copy would not resolve, and the hash-verified bytes must stay
self-contained. A handful of such files therefore derive the flag themselves
on purpose, and that is not drift: every ``src`` catalogued in
``.fleet.toml``'s ``[components]`` table (read via
``scripts/vendored_catalog.py``) is a vendor-verbatim publication, and
``tests/test_no_window_convention.py`` derives its allow-list from that same
catalog.

A long-lived child that is later stopped with ``CTRL_BREAK_EVENT`` takes
``NEW_GROUP_NO_WINDOW`` instead: its own process group, so the signal reaches
only that child, plus no console window. Never add ``DETACHED_PROCESS`` to
either flag (``docs/long-lived-process-conventions.md``).

Stdlib-only and side-effect-free, so a standalone script launched outside the
project venv can import it after putting the repo root on ``sys.path``.
"""

from __future__ import annotations

import subprocess
import sys

#: ``subprocess.CREATE_NO_WINDOW`` on Windows, ``0`` (a no-op) elsewhere, so
#: the same call site is portable without a per-platform branch.
NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

#: ``CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`` on Windows, ``0`` elsewhere:
#: the flags for a child torn down with ``CTRL_BREAK_EVENT``, which is scoped
#: to a process group and would otherwise also reach this process.
NEW_GROUP_NO_WINDOW: int = (
    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    if sys.platform == "win32"
    else 0
)

__all__ = ["NEW_GROUP_NO_WINDOW", "NO_WINDOW"]
