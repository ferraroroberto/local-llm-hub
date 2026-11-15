"""A watch's rolling sample history as JSON rows in one SQLite file.

The resource watch and the port watch each keep one; a sample is stored
keyed by its timestamp and read back exactly as it was taken. A subclass
names its default file (resolved per call, so a test or an env override can
redirect the module default) and any extra tables it needs.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..fsio.runtime_files import connect_sqlite, make_dirs

_SAMPLES_SCHEMA = "CREATE TABLE IF NOT EXISTS samples (ts REAL PRIMARY KEY, body TEXT NOT NULL);\n"


class SampleStore:
    """Thread-safe by construction: every call opens its own short connection."""

    EXTRA_SCHEMA = ""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None

    def _default_path(self) -> Path:
        raise NotImplementedError

    @property
    def path(self) -> Path:
        return self._path or self._default_path()

    def _connect(self) -> sqlite3.Connection:
        make_dirs(self.path.parent)
        conn = connect_sqlite(self.path, timeout=5)
        conn.executescript(_SAMPLES_SCHEMA + self.EXTRA_SCHEMA)
        return conn

    def _add_sample(self, sample: Dict[str, Any], keep_s: float) -> None:
        """Append one sample and drop everything older than ``keep_s`` seconds before it."""
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO samples (ts, body) VALUES (?, ?)",
                         (sample["ts"], json.dumps(sample, separators=(",", ":"))))
            conn.execute("DELETE FROM samples WHERE ts < ?", (sample["ts"] - keep_s,))

    def samples_since(self, since_ts: float) -> List[Dict[str, Any]]:
        """Samples at or after ``since_ts``, oldest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT body FROM samples WHERE ts >= ? ORDER BY ts", (since_ts,)).fetchall()
        return [json.loads(body) for (body,) in rows]
