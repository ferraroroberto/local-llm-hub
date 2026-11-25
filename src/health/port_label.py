"""A short, public-safe label for a port holder: its repo and entry point.

Every fleet repo runs ``python.exe``, so a process name alone attributes nothing.
For an interpreter the label is the basename of the git repo its working directory
sits in plus the module or script it runs (``local-llm-hub: -m pytest``). It never
holds a full path, an argument or inline code. ``None`` means the process could not
be read (it exited, or access was denied), which is not the same as "no repo".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, List, Optional

import psutil

INTERPRETERS = frozenset({"python.exe", "pythonw.exe", "node.exe"})
_MAX_LABEL = 80
_PY_VALUE_FLAGS = frozenset({"-W", "-X"})  # python options whose next argument is not the script
_SEPARATORS = re.compile(r"[\\/]")

OpenProcess = Callable[[int], "psutil.Process"]


def _basename(text: str) -> str:
    return _SEPARATORS.split(text.rstrip("\\/"))[-1]


def _entry_point(args: List[str]) -> Optional[str]:
    """``-m module``, ``-c`` or the script's basename from an interpreter's arguments."""
    skip = False
    for i, arg in enumerate(args):
        if skip:
            skip = False
        elif arg == "-m" and i + 1 < len(args):
            return f"-m {_basename(args[i + 1])}"
        elif arg == "-c":
            return "-c"  # the code itself may hold anything: name the flag, never the text
        elif arg in _PY_VALUE_FLAGS:
            skip = True
        elif not arg.startswith("-"):
            return _basename(arg)
    return None


def _repo_name(cwd: Path) -> Optional[str]:
    """The folder name of the nearest ancestor of ``cwd`` that holds ``.git`` (a folder, or a file in a worktree)."""
    for folder in (cwd, *cwd.parents):
        if (folder / ".git").exists():
            return folder.name or None
    return None


def process_label(pid: int, name: str, open_process: OpenProcess = psutil.Process) -> Optional[str]:
    """The repo and entry point of interpreter ``pid``, or ``None`` for any other process or one
    that cannot be read. Blocking: it reads the process and walks up from its working directory."""
    if name.lower() not in INTERPRETERS:
        return None
    try:
        proc = open_process(pid)
        cwd = Path(proc.cwd())
        args = proc.cmdline()[1:]
    except (psutil.Error, OSError):
        return None
    parts = [p for p in (_repo_name(cwd), _entry_point(args)) if p]
    if not parts:
        return None
    label = parts[0] if len(parts) == 1 else f"{parts[0]}: {parts[1]}"
    return label[:_MAX_LABEL]
