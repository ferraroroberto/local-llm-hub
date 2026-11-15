"""On-disk history of port samples and Tcpip exhaustion events.

One SQLite file under the runtime-data root (``src/runtime_data.py``) beside
the resource watch's, so the incident before a reboot or a hub restart is
still there to read afterwards. Events are keyed by the System log's record
id, so re-reading an overlapping window never stores one twice.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..fsio.runtime_files import connect_sqlite, make_dirs
from ..runtime_data import runtime_db_path

DEFAULT_DB_PATH = runtime_db_path("local-llm-hub", "port_watch.sqlite3", env_var="LLM_HUB_PORT_WATCH_DB_PATH")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (ts REAL PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (record_id INTEGER PRIMARY KEY, ts REAL NOT NULL, body TEXT NOT NULL);
"""


class PortStore:
    """Thread-safe by construction: every call opens its own short connection."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None

    @property
    def path(self) -> Path:
        # Resolved per call so a test (or env override) can redirect the module default.
        return self._path or DEFAULT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        make_dirs(self.path.parent)
        conn = connect_sqlite(self.path, timeout=5)
        conn.executescript(_SCHEMA)
        return conn

    def add_sample(self, sample: Dict[str, Any], keep_hours: float) -> None:
        """Append one sample and drop everything older than ``keep_hours``."""
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO samples (ts, body) VALUES (?, ?)",
                         (sample["ts"], json.dumps(sample, separators=(",", ":"))))
            conn.execute("DELETE FROM samples WHERE ts < ?", (sample["ts"] - keep_hours * 3600.0,))

    def samples_since(self, since_ts: float) -> List[Dict[str, Any]]:
        """Samples at or after ``since_ts``, oldest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT body FROM samples WHERE ts >= ? ORDER BY ts", (since_ts,)).fetchall()
        return [json.loads(body) for (body,) in rows]

    def add_events(self, events: List[Dict[str, Any]], *, now: float, keep_days: float) -> None:
        """Store events not seen before and drop those older than ``keep_days``."""
        with closing(self._connect()) as conn, conn:
            conn.executemany("INSERT OR IGNORE INTO events (record_id, ts, body) VALUES (?, ?, ?)",
                             [(e["record_id"], e["ts"], json.dumps(e, separators=(",", ":"))) for e in events])
            conn.execute("DELETE FROM events WHERE ts < ?", (now - keep_days * 86400.0,))

    def events_since(self, since_ts: float) -> List[Dict[str, Any]]:
        """Events at or after ``since_ts``, oldest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT body FROM events WHERE ts >= ? ORDER BY ts", (since_ts,)).fetchall()
        return [json.loads(body) for (body,) in rows]
