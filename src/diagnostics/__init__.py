"""On-demand machine diagnostics (issue #315).

A capture *run* records what this machine is actually doing — system-level
CPU/RAM/swap/disk/net/GPU plus a full per-process inventory and the listening-
port map — into a SQLite store, then interprets it: fleet-aware attribution
(which app owns which processes), a rules-based health verdict, and drift
against a marked baseline.

The design constraint that shapes every module here: **no new resident
process**. The sampler is an asyncio task inside the already-running hub, so
nothing exists when no capture is active. Everything is pure ``psutil`` +
stdlib ``sqlite3`` (plus the existing ``nvidia-smi`` probe), so the identical
capture runs on Windows, macOS, and Linux.

Module seams:

  * :mod:`~src.diagnostics.store`       — SQLite schema, migrations, retention
  * :mod:`~src.diagnostics.attribution` — process → fleet-app mapping, ports
  * :mod:`~src.diagnostics.sampler`     — the in-hub capture loop
  * :mod:`~src.diagnostics.rules`       — health-verdict engine
  * :mod:`~src.diagnostics.report`      — summaries, drift, markdown export
"""

from __future__ import annotations

__all__ = ["attribution", "coverage", "ingest", "report", "rules", "sampler", "store"]
