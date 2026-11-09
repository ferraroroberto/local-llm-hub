"""Writers for runtime files whose path the runtime-write ratchet cannot resolve.

A log, database, staging folder or install target is named by a parameter, a
config value, a temp-dir variable or a CLI argument, so the ratchet cannot follow
the path statically and each write would need its own exempt marker. These
writers take the path, refuse it at runtime when it resolves under one of the
code folders the ratchet guards, then write: the guarantee a marker only claimed
is enforced here, and this module carries the one stated reason.

Stdlib-only with no import from the rest of the repo, so any module (and the
installers under ``scripts/``) can use it.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import IO, Any, Union

PathLike = Union[str, "os.PathLike[str]"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Mirrors ``[runtime_write_ratchet] code_folders`` in ``.fleet.toml`` (a test pins the two together).
CODE_FOLDERS = ("src", "app_web", "tray", "scripts")


def checked(path: PathLike) -> Path:
    """``path`` resolved, or ``ValueError`` when it sits under a code folder."""
    resolved = Path(path).expanduser().resolve()
    for name in CODE_FOLDERS:
        folder = REPO_ROOT / name
        if resolved == folder or folder in resolved.parents:
            raise ValueError(
                f"Refusing to write under the code folder {folder}: {resolved}. "
                "Runtime output belongs in data/, webapp/, config/ or the runtime data root."
            )
    return resolved


def make_dirs(path: PathLike, *, parents: bool = True, exist_ok: bool = True) -> Path:
    """Create the folder ``path``; refuses a path under a code folder."""
    target = checked(path)
    # runtime-write-exempt: caller-named folder; checked() above raises when it is under a code folder
    target.mkdir(parents=parents, exist_ok=exist_ok)
    return target


def open_for_write(path: PathLike, mode: str = "w", **kwargs: Any) -> IO[Any]:
    """Open ``path`` with ``mode`` (creating no folders); refuses a path under a code folder."""
    target = checked(path)
    # runtime-write-exempt: caller-named file; checked() above raises when it is under a code folder
    return target.open(mode, **kwargs)


def save_text(path: PathLike, text: str, encoding: str = "utf-8", **kwargs: Any) -> None:
    """Write ``text`` to ``path`` in one call; refuses a path under a code folder."""
    with open_for_write(path, "w", encoding=encoding, **kwargs) as handle:
        handle.write(text)


def save_bytes(path: PathLike, data: bytes) -> None:
    """Write ``data`` to ``path`` in one call; refuses a path under a code folder."""
    with open_for_write(path, "wb") as handle:
        handle.write(data)


def copy_file(src: PathLike, dst: PathLike) -> Path:
    """Copy the file ``src`` to ``dst``; refuses a destination under a code folder."""
    target = checked(dst)
    with open(src, "rb") as source, open_for_write(target, "wb") as sink:
        shutil.copyfileobj(source, sink)
    shutil.copymode(src, target)
    return target


def move(src: PathLike, dst: PathLike) -> Path:
    """Move ``src`` to ``dst``; refuses a destination under a code folder."""
    target = checked(dst)
    # runtime-write-exempt: caller-named destination; checked() above raises when it is under a code folder
    shutil.move(str(src), str(target))
    return target


def file_handler(path: PathLike, mode: str = "a", encoding: str = "utf-8") -> logging.FileHandler:
    """A ``logging.FileHandler`` on ``path``; refuses a path under a code folder."""
    target = checked(path)
    # runtime-write-exempt: caller-named log file; checked() above raises when it is under a code folder
    return logging.FileHandler(target, mode=mode, encoding=encoding)


def connect_sqlite(path: PathLike, **kwargs: Any) -> sqlite3.Connection:
    """Open (creating on first use) the SQLite file ``path``; refuses a path under a code folder."""
    target = checked(path)
    # runtime-write-exempt: caller-named database; checked() above raises when it is under a code folder
    return sqlite3.connect(target, **kwargs)
