"""Per-collector coverage — did we actually get to measure this? (issue #322)

A health tool must never let *"we couldn't measure this"* read as *"this is
fine."* Two collectors degrade silently on macOS, where the hub lacks the
privilege to read other users' data:

- **ports** — ``psutil.net_connections`` raises ``AccessDenied``, so the scan
  returns an empty list. Empty-because-denied and empty-because-nothing-listens
  are identical once stored, so this one signal is captured at collection time.
- **per-process memory / CPU** — ``memory_info``/``cpu_percent`` are denied for
  other users' processes, stored as NULL (never 0). Reconstructable after the
  fact by counting NULLs, so this is computed at finalize from the stored rows.

The result is a small map persisted on the run and surfaced in the report and
the verdict. Health and coverage are **orthogonal axes**: the verdict ``level``
stays the health of what we *could* measure; coverage rides alongside it. That
keeps a blind macOS run from being pinned at ``warning`` forever (the cry-wolf
failure #315 fought) while still refusing to call it a clean ``healthy``.

This module is the single source of the coverage vocabulary and of which
verdict rules depend on which collector, so the report, the rules, and the
export never drift apart.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from . import store

# Coverage status vocabulary. `ok` and `unsupported` are both fine — the latter
# is a structural absence (no discrete VRAM on Apple-silicon unified memory),
# not a gap we could close. `partial`/`denied` are the ones that qualify a
# verdict.
OK = "ok"
PARTIAL = "partial"
DENIED = "denied"
UNSUPPORTED = "unsupported"

# A collector whose status is one of these was blind for some or all of the run
# and must visibly qualify the verdict.
_BLIND = {PARTIAL, DENIED}

# Which verdict rule cannot be trusted when which collector is blind. Consumed
# by rules.py so a rule that could not run reports "not evaluated" instead of a
# silent pass.
RULE_DEPENDS_ON = {
    "ports.duplicate": "ports",
}

# Below this readable fraction a per-process signal is called `partial` rather
# than `ok` — a couple of unreadable helper processes is not worth a caveat,
# but the macOS ~58%-readable case very much is.
_PARTIAL_FLOOR = 0.98


def _darwin() -> bool:
    return sys.platform == "darwin"


def compute(run_id: str, *, ports_denied: bool) -> Dict[str, Any]:
    """Build the coverage map for a finished run.

    ``ports_denied`` is the collection-time signal (the sampler saw every port
    scan raise ``AccessDenied``); everything else is derived from the stored
    rows and the platform, so a re-computation is deterministic."""
    read = store.proc_readability(run_id)
    total = read["total"]

    cov: Dict[str, Any] = {
        "ports": {"status": DENIED if ports_denied else OK},
        "proc_mem": _fraction_status(read["mem_ok"], total),
        "proc_cpu": _fraction_status(read["cpu_ok"], total),
    }
    # Unified memory exposes no discrete VRAM figure, so GPU-memory pressure is
    # structurally invisible on Apple silicon — recorded as a known gap, not a
    # failure. (A positive ANE/unified-pressure signal is its own feature.)
    if _darwin():
        cov["gpu"] = {"status": UNSUPPORTED}
    return cov


def _fraction_status(readable: int, total: int) -> Dict[str, Any]:
    if total <= 0:
        return {"status": OK, "readable": 0, "total": 0}
    status = OK if readable >= total * _PARTIAL_FLOOR else PARTIAL
    return {"status": status, "readable": readable, "total": total}


def is_degraded(coverage: Dict[str, Any]) -> bool:
    """True if any collector was blind — the verdict must not read as a plain
    ``healthy`` while this holds."""
    return any((c or {}).get("status") in _BLIND for c in (coverage or {}).values())


def blind_collectors(coverage: Dict[str, Any]) -> List[str]:
    return [name for name, c in (coverage or {}).items()
            if (c or {}).get("status") in _BLIND]


def collector_status(coverage: Dict[str, Any], collector: str) -> str:
    return ((coverage or {}).get(collector) or {}).get("status", OK)


def describe(collector: str, entry: Dict[str, Any]) -> str:
    """One human line for the report's coverage section."""
    status = (entry or {}).get("status", OK)
    if status == OK:
        return "collected"
    if status == UNSUPPORTED:
        return "not applicable on this platform"
    if status == DENIED:
        return "not collected — insufficient privileges"
    if status == PARTIAL:
        readable, total = entry.get("readable", 0), entry.get("total", 0)
        unread = max(0, total - readable)
        return (f"partially collected — {readable}/{total} processes readable "
                f"({unread} denied)")
    return status
