"""On-disk history of port samples and Tcpip exhaustion events.

One SQLite file under the runtime-data root (``src/runtime_data.py``) beside
the resource watch's, so the incident before a reboot or a hub restart is
still there to read afterwards. Events are keyed by the System log's record
id, so re-reading an overlapping window never stores one twice.
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List

from ..runtime_data import runtime_db_path
from .sample_store import SampleStore

DEFAULT_DB_PATH = runtime_db_path("local-llm-hub", "port_watch.sqlite3", env_var="LLM_HUB_PORT_WATCH_DB_PATH")


class PortStore(SampleStore):
    EXTRA_SCHEMA = "CREATE TABLE IF NOT EXISTS events (record_id INTEGER PRIMARY KEY, ts REAL NOT NULL, body TEXT NOT NULL);\n"

    def _default_path(self) -> Path:
        return DEFAULT_DB_PATH

    def add_sample(self, sample: Dict[str, Any], keep_hours: float) -> None:
        self._add_sample(sample, keep_hours * 3600.0)

    def add_events(self, events: List[Dict[str, Any]], *, now: float, keep_days: float) -> None:
        """Store events not seen before and drop those older than ``keep_days``."""
        with closing(self._connect()) as conn, conn:
            conn.executemany("INSERT OR IGNORE INTO events (record_id, ts, body) VALUES (?, ?, ?)",
                             [(e["record_id"], e["ts"], json.dumps(e, separators=(",", ":"))) for e in events])
            conn.execute("DELETE FROM events WHERE ts < ?", (now - keep_days * 86400.0,))

    def update_event(self, event: Dict[str, Any]) -> None:
        """Replace a stored event: a trace read after the event was first stored."""
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO events (record_id, ts, body) VALUES (?, ?, ?)",
                         (event["record_id"], event["ts"], json.dumps(event, separators=(",", ":"))))

    def events_since(self, since_ts: float) -> List[Dict[str, Any]]:
        """Events at or after ``since_ts``, oldest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT body FROM events WHERE ts >= ? ORDER BY ts", (since_ts,)).fetchall()
        return [json.loads(body) for (body,) in rows]
