"""Atomic text-file write and tolerant JSON read shared by the hub's JSON
config / state stores."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


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


def read_json(path: Path, what: str) -> Optional[Any]:
    """Parse the UTF-8 JSON file at ``path``; ``None`` when it can't be read.

    The read half of :func:`atomic_write_text` for stores whose callers fall
    back to a default rather than fail: a missing file returns ``None``
    silently (the normal fresh-clone state); an unreadable or malformed one
    logs a warning naming ``what`` it was and returns ``None``. A file holding
    the JSON literal ``null`` is indistinguishable from a failure — callers
    that need a typed result check ``isinstance`` on the answer anyway.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("⚠️ could not load %s %s: %s", what, path, exc)
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("⚠️ could not load %s %s: %s", what, path, exc)
        return None
