"""Host-side Pi usage parser.

Reads the session logs Pi writes under
``~/.pi/agent/sessions/<encoded-cwd>/<timestamp>_<id>.jsonl`` and emits usage
records in the shared ``UsageRecord`` shape (tagged ``vendor="pi"``).

Design constraints mirror ``codex_usage.py``: read-only, zero subprocesses,
passive, and an mtime cache so a file is only re-parsed when it changes.

Token mapping — one ``UsageRecord`` per assistant message that carries
``message.usage`` (a per-turn figure, never cumulative)::

    usage.input       -> input_tokens           (excludes cache reads)
    usage.output      -> output_tokens
    usage.cacheRead   -> cache_read_tokens
    usage.cacheWrite  -> cache_creation_tokens

``usage.input`` already excludes cache reads for every provider Pi drives
(``totalTokens == input + output + cacheRead + cacheWrite``), so unlike Codex
no subtraction is needed. The project comes from the session header's ``cwd``,
encoded into the same key Claude uses so one repo groups across vendors.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from src.usage.usage_common import (
    FileStats,
    UsageRecord,
    encode_project_key,
    iter_jsonl,
    load_cached,
    parse_iso_ts,
    project_pretty,
)

_log = logging.getLogger(__name__)

_PI_SESSIONS_DIR: Path = Path.home() / ".pi" / "agent" / "sessions"

# File-level mtime cache (module-level singleton), independent of the others.
_file_cache: Dict[str, FileStats] = {}


def _parse_session_file(path: Path) -> List[UsageRecord]:
    """Parse one Pi session JSONL file and return usage records."""
    records: List[UsageRecord] = []
    cwd: Optional[str] = None
    session_id = path.stem

    try:
        for obj in iter_jsonl(path):
            etype = obj.get("type")

            if etype == "session":
                session_id = obj.get("id") or session_id
                if obj.get("cwd"):
                    cwd = obj["cwd"]
                continue

            if etype != "message":
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if msg.get("role") != "assistant" or not isinstance(usage, dict):
                continue

            key = encode_project_key(cwd) if cwd else "pi"
            records.append(
                UsageRecord(
                    session_id=session_id,
                    project_key=key,
                    project_name=project_pretty(key) if cwd else "(unknown)",
                    model=msg.get("model") or "unknown",
                    ts=parse_iso_ts(obj.get("timestamp")),
                    input_tokens=int(usage.get("input") or 0),
                    output_tokens=int(usage.get("output") or 0),
                    cache_creation_tokens=int(usage.get("cacheWrite") or 0),
                    cache_read_tokens=int(usage.get("cacheRead") or 0),
                    vendor="pi",
                )
            )
    except OSError as exc:
        _log.warning("⚠️ pi_usage: cannot read %s: %s", path, exc)
    return records


def all_records() -> List[UsageRecord]:
    """Scan every Pi session file and return every usage record."""
    records: List[UsageRecord] = []

    if not _PI_SESSIONS_DIR.exists():
        return records

    try:
        session_files = list(_PI_SESSIONS_DIR.glob("*/*.jsonl"))
    except OSError as exc:
        _log.warning("⚠️ pi_usage: cannot scan %s: %s", _PI_SESSIONS_DIR, exc)
        return records

    for sf in session_files:
        records.extend(load_cached(sf, _file_cache, _parse_session_file))

    return records
