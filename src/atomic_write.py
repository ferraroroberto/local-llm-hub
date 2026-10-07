"""Atomic text-file write shared by the hub's JSON config / state stores."""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a sibling ``<name>.tmp`` + ``os.replace``.

    Creates missing parent directories. A reader never sees a half-written
    file; a crash mid-write leaves only the stray ``.tmp``. UTF-8, like every
    JSON store that calls this.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
