"""Worker pool sizes: the six defaults, their bounds, and the owner's saved overrides.

``config/worker_pools.json`` (gitignored, ``{"hot-poll": 8, ...}``) holds the
sizes the owner chose in Settings. :mod:`.pools` reads it once at import,
because an executor's size is fixed when it is built, so a saved size applies
at the next hub restart.

A missing, unreadable or malformed file, or one entry that is out of range,
never stops the hub: the affected pool keeps its default and a warning names
why. Saving is stricter: :func:`validate` refuses a bad value with a message
the Settings sheet shows as-is.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from ..atomic_write import atomic_write_text, read_json

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = PROJECT_ROOT / "config" / "worker_pools.json"

MIN_SIZE = 1
# A pool is an upper bound on threads for blocking work; past this the box is
# short of memory and ports long before the work gets faster.
MAX_SIZE = 64


@dataclass(frozen=True)
class PoolSpec:
    """One pool's id, its Settings label, what it runs, and its default size."""

    id: str
    label: str
    description: str
    default: int


SPECS: tuple[PoolSpec, ...] = (
    PoolSpec("hot-poll", "Hot poll", "Status reads a page polls: listening ports, health probes, log tails, cached summaries.", 8),
    PoolSpec("heavy-read", "Heavy read", "Reads that walk a database or the disk to build a report: rare and slow.", 4),
    PoolSpec("action", "Action", "Work a person starts and waits on: backend and service start or stop, installs, config writes.", 12),
    PoolSpec("fan-out", "Fan-out", "Calls to other machines: SSH, wake-on-LAN, peer reads.", 8),
    PoolSpec("background", "Background", "The hub's own loops: watchers, samplers, failover, idle unload.", 6),
    PoolSpec("media", "Media", "Audio and image work for an inference request: decode, transcription, generation.", 8),
)

DEFAULTS: Dict[str, int] = {spec.id: spec.default for spec in SPECS}


def validate(sizes: Mapping[str, Any]) -> Dict[str, int]:
    """The sizes as ints, or ``ValueError`` naming the first pool that is not a whole number in range."""
    clean: Dict[str, int] = {}
    for pool_id, value in sizes.items():
        if pool_id not in DEFAULTS:
            raise ValueError(f"unknown pool {pool_id!r}; the pools are {', '.join(DEFAULTS)}")
        # bool is an int subclass: ``true`` is not a size.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{pool_id}: size must be a whole number from {MIN_SIZE} to {MAX_SIZE}, got {value!r}")
        if not MIN_SIZE <= value <= MAX_SIZE:
            raise ValueError(f"{pool_id}: size must be from {MIN_SIZE} to {MAX_SIZE}, got {value}")
        clean[pool_id] = value
    return clean


def load_saved(path: Optional[Path] = None) -> Dict[str, int]:
    """The owner's saved sizes, minus anything unusable (each such entry logs and falls back to its default)."""
    data = read_json(path or DEFAULT_PATH, "worker pool sizes")
    if data is None:
        return {}
    if not isinstance(data, dict):
        logger.warning("⚠️ worker pool sizes: top level is %s, not an object; using defaults", type(data).__name__)
        return {}
    saved: Dict[str, int] = {}
    for pool_id, value in data.items():
        try:
            saved.update(validate({pool_id: value}))
        except ValueError as exc:
            logger.warning("⚠️ worker pool sizes: %s; using the default", exc)
    return saved


def effective_sizes(path: Optional[Path] = None) -> Dict[str, int]:
    """Every pool's size: the saved one, else the default."""
    return {**DEFAULTS, **load_saved(path)}


def save(sizes: Mapping[str, Any], path: Optional[Path] = None) -> Dict[str, int]:
    """Validate ``sizes``, merge them over the saved ones, write the file; returns what is saved."""
    clean = validate(sizes)
    merged = {**load_saved(path), **clean}
    atomic_write_text(path or DEFAULT_PATH, json.dumps(merged, indent=2) + "\n")
    logger.info("💾 Saved worker pool sizes %s; they apply at the next hub restart", merged)
    return merged
