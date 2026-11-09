"""The hub's own record of which Mac result images it has delivered.

The Mac keeps nothing about delivery (it prunes its run folders on a plain
14-day clock), so the puller's only memory is this SQLite file, resolved
through the fleet runtime-data convention rather than a repo-relative path.

Three tables, because a run can fail half-way: ``sent_files`` is written one
image at a time as each send is confirmed, so a retry resends only what never
landed, and ``delivered_runs`` is written once every image of the run is
confirmed. A run is never recorded as delivered on anything short of that.
``filed_audits`` follows the same rule for the routing audit: written only once
the filing tool confirmed an outcome, so an unconfirmed filing is retried. The
nightly-CI record (``ci_reported``, ``flaky_nights``) is at the end of the file.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

from ..fsio.runtime_files import connect_sqlite
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
    conn = connect_sqlite(db)
    conn.executescript(_SCHEMA)
    conn.executescript(_CI_SCHEMA)
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


# ------------------------------------------------------------ nightly CI record
#
# ``ci_reported`` is written once a night's issue outcome (and any flaky filing)
# is confirmed; ``digested`` flips when the owner's one-line digest for it is
# confirmed sent. ``flaky_nights`` is the host-local flake ledger: one row per
# (repo, run, test id), so a re-read of the same night never counts twice.

_CI_SCHEMA = """
CREATE TABLE IF NOT EXISTS ci_reported (
    repo TEXT NOT NULL,
    run_id TEXT NOT NULL,
    result TEXT NOT NULL,
    flaky INTEGER NOT NULL,
    night_at REAL NOT NULL,
    reported_at REAL NOT NULL,
    digested INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, run_id)
);
CREATE TABLE IF NOT EXISTS flaky_nights (
    repo TEXT NOT NULL,
    run_id TEXT NOT NULL,
    test_id TEXT NOT NULL,
    night_at REAL NOT NULL,
    PRIMARY KEY (repo, run_id, test_id)
);
"""


def ci_reported(conn: sqlite3.Connection, repo: str, run_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM ci_reported WHERE repo = ? AND run_id = ?", (repo, run_id)).fetchone()
    return row is not None


def newest_reported_night(conn: sqlite3.Connection, repo: str) -> float:
    """Unix time of the newest night already reported for the repo (0 when none)."""
    row = conn.execute("SELECT MAX(night_at) FROM ci_reported WHERE repo = ?", (repo,)).fetchone()
    return float(row[0] or 0)


def mark_ci_reported(conn: sqlite3.Connection, repo: str, run_id: str, result: str, flaky: int, night_at: float) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO ci_reported (repo, run_id, result, flaky, night_at, reported_at) VALUES (?, ?, ?, ?, ?, ?)",
            (repo, run_id, result, flaky, night_at, time.time()),
        )


def undigested_nights(conn: sqlite3.Connection) -> list:
    """``(repo, run_id, result, flaky)`` of every reported night not yet in a sent digest."""
    return conn.execute(
        "SELECT repo, run_id, result, flaky FROM ci_reported WHERE digested = 0 ORDER BY night_at, repo"
    ).fetchall()


def mark_digested(conn: sqlite3.Connection, nights: list) -> None:
    with conn:
        conn.executemany("UPDATE ci_reported SET digested = 1 WHERE repo = ? AND run_id = ?", [(n[0], n[1]) for n in nights])


def record_flaky(conn: sqlite3.Connection, repo: str, run_id: str, test_ids: list, night_at: float) -> None:
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO flaky_nights (repo, run_id, test_id, night_at) VALUES (?, ?, ?, ?)",
            [(repo, run_id, t, night_at) for t in test_ids],
        )


def flaky_night_count(conn: sqlite3.Connection, repo: str, test_id: str, since: float) -> int:
    """Distinct nights on which the test was flaky, from ``since`` on."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT run_id) FROM flaky_nights WHERE repo = ? AND test_id = ? AND night_at >= ?",
        (repo, test_id, since),
    ).fetchone()
    return int(row[0])


def prune_ci(conn: sqlite3.Connection, before: float) -> None:
    with conn:
        conn.execute("DELETE FROM flaky_nights WHERE night_at < ?", (before,))
        conn.execute("DELETE FROM ci_reported WHERE night_at < ? AND digested = 1", (before,))
