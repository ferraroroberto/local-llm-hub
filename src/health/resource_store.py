"""Rolling sample history and alert debounce state for the resource watch.

One SQLite file under the machine-stable runtime-data directory
(``src/runtime_data.py``): on the tower that is the NVMe, never the SMR disk
the watch exists to measure. Writing a minute-by-minute history to the slow
drive would add to the very latency it records.

A sample is one JSON row keyed by its timestamp, so the analysis reads it back
exactly as it was taken. Alert state lives here too, so a hub restart cannot
re-send an alert that is still inside its cooldown.
"""

from __future__ import annotations

import logging
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Optional

from ..runtime_data import runtime_db_path
from .sample_store import SampleStore

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = runtime_db_path("local-llm-hub", "resource_watch.sqlite3", env_var="LLM_HUB_RESOURCE_WATCH_DB_PATH")


class ResourceStore(SampleStore):
    EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_state (
    key TEXT PRIMARY KEY, last_sent REAL, last_attempt REAL, last_error TEXT
);
"""

    def _default_path(self) -> Path:
        return DEFAULT_DB_PATH

    def add_sample(self, sample: Dict[str, Any], keep_days: float) -> None:
        self._add_sample(sample, keep_days * 86400.0)

    def alert_state(self, key: str) -> Dict[str, Optional[float]]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT last_sent, last_attempt FROM alert_state WHERE key = ?", (key,)).fetchone()
        return {"last_sent": row[0], "last_attempt": row[1]} if row else {"last_sent": None, "last_attempt": None}

    def record_alert(self, key: str, *, ts: float, sent: bool, error: Optional[str] = None) -> None:
        """Remember a delivery attempt; only a successful one starts the cooldown."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO alert_state (key, last_sent, last_attempt, last_error) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET last_attempt = excluded.last_attempt, last_error = excluded.last_error, "
                "last_sent = COALESCE(excluded.last_sent, alert_state.last_sent)",
                (key, ts if sent else None, ts, error),
            )
