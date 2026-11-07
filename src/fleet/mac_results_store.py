"""The hub's own record of which Mac result images it has delivered.

The Mac keeps nothing about delivery (it prunes its run folders on a plain
14-day clock), so the puller's only memory is this SQLite file, resolved
through the fleet runtime-data convention rather than a repo-relative path.

Three tables, because a run can fail half-way: ``sent_files`` is written one
image at a time as each send is confirmed, so a retry resends only what never
landed, and ``delivered_runs`` is written once every image of the run is
confirmed. A run is never recorded as delivered on anything short of that.
``filed_audits`` follows the same rule for the routing audit: written only once
the filing tool confirmed an outcome, so an unconfirmed filing is retried.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

from ..runtime_data import runtime_db_path

DB_ENV = "MAC_RESULTS_DB_PATH"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_files (
    repo TEXT NOT NULL,
    run_id TEXT NOT NULL,
    file_key TEXT NOT NULL,
    sent_at REAL NOT NULL,
    PRIMARY KEY (repo, run_id, file_key)
);
CREATE TABLE IF NOT EXISTS delivered_runs (
    repo TEXT NOT NULL,
    run_id TEXT NOT NULL,
    files INTEGER NOT NULL,
    delivered_at REAL NOT NULL,
    PRIMARY KEY (repo, run_id)
);
CREATE TABLE IF NOT EXISTS filed_audits (
    repo TEXT NOT NULL,
    run_id TEXT NOT NULL,
    state TEXT NOT NULL,
    filed_at REAL NOT NULL,
    PRIMARY KEY (repo, run_id)
);
"""


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (creating on first use) the delivery store."""
    db = path or runtime_db_path("local-llm-hub", "mac_results.sqlite3", env_var=DB_ENV, create=True)
    # runtime-write-exempt: db is runtime_db_path() or a caller-supplied path (tests), both outside the tree
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    return conn


def run_delivered(conn: sqlite3.Connection, repo: str, run_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM delivered_runs WHERE repo = ? AND run_id = ?", (repo, run_id)
    ).fetchone()
    return row is not None


def file_sent(conn: sqlite3.Connection, repo: str, run_id: str, file_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sent_files WHERE repo = ? AND run_id = ? AND file_key = ?",
        (repo, run_id, file_key),
    ).fetchone()
    return row is not None


def mark_file_sent(conn: sqlite3.Connection, repo: str, run_id: str, file_key: str) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_files (repo, run_id, file_key, sent_at) VALUES (?, ?, ?, ?)",
            (repo, run_id, file_key, time.time()),
        )


def mark_run_delivered(conn: sqlite3.Connection, repo: str, run_id: str, files: int) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO delivered_runs (repo, run_id, files, delivered_at) VALUES (?, ?, ?, ?)",
            (repo, run_id, files, time.time()),
        )


def audit_filed(conn: sqlite3.Connection, repo: str, run_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM filed_audits WHERE repo = ? AND run_id = ?", (repo, run_id)
    ).fetchone()
    return row is not None


def mark_audit_filed(conn: sqlite3.Connection, repo: str, run_id: str, state: str) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO filed_audits (repo, run_id, state, filed_at) VALUES (?, ?, ?, ?)",
            (repo, run_id, state, time.time()),
        )
