"""Fleet-wide desired-state placement — which models should run on which host.

Step 2 of the always-on control plane (#353). The tower (control node) holds a
single ``config/fleet_placement.json`` mapping ``{host_id: [model_id, ...]}`` —
the models that *should* be running on each machine, including the tower's own.
A background reconcile loop (``src/fleet_reconcile.py``) enforces it: waking
offline satellites and starting any placed model that isn't up. Toggling a model
onto a machine that's powered off is remembered here and applied when it next
reports in.

Mirrors ``src/startup_profile.py`` (#265/#304) with one deliberate divergence:
the live JSON is **gitignored** (the placement grid rewrites it on every toggle,
so tracking it would dirty the tree), and the committed
``config/fleet_placement.example.json`` is a **copy-me template**, *not* an
auto-read fallback. Unlike the startup profile — whose example seeds a fresh
clone's *local* autostart (low blast radius) — an absent placement here must
mean "the fleet has no desired state yet", so the reconcile loop no-ops rather
than enforcing an example across *other people's machines* (waking satellites,
starting models nobody placed). Activate placement deliberately: copy the
template to ``config/fleet_placement.json``, or PATCH the API. Load is tolerant
(a broken file never raises — the reconcile loop must never be kept from
starting); save is validated + atomic + cache-busting.

Per-host model ids are validated against *that host's* launchable set
(``model_registry.launchable_local_ids(get_host(host_id))``) exactly as the
startup profile validates against the active host's — a stale/typo'd id is
dropped rather than persisted to silently no-op at reconcile time. Unknown host
ids are rejected outright (a structural error, not a stale model entry).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLACEMENT_PATH = PROJECT_ROOT / "config" / "fleet_placement.json"
# Committed **copy-me template** documenting the shape — deliberately NOT an
# auto-read fallback (see module docstring): an absent live file means "no fleet
# desired state", so the reconcile loop stays inert until placement is set.
EXAMPLE_PLACEMENT_PATH = PROJECT_ROOT / "config" / "fleet_placement.example.json"

# Parsed cache keyed by the resolved path (same shape as
# startup_profile._PROFILE_CACHE) so swapping DEFAULT_PLACEMENT_PATH in tests
# transparently busts the cache instead of returning a stale hit.
_PLACEMENT_CACHE: Dict[str, Dict[str, List[str]]] = {}


def _coerce(data: Any) -> Dict[str, List[str]]:
    """Best-effort shape a loaded JSON blob into ``{host_id: [str, ...]}``.

    Tolerant by construction — ``load_fleet_placement`` must never raise, so a
    non-dict blob becomes ``{}``, a non-list value is dropped, and non-string
    ids are stringified/filtered. Validation against real hosts/models happens
    only on *save* (``normalize_placement``), never on load.
    """
    if not isinstance(data, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for host_id, ids in data.items():
        if not isinstance(ids, list):
            continue
        out[str(host_id)] = [str(m) for m in ids if m]
    return out


def load_fleet_placement(path: Optional[str] = None) -> Dict[str, List[str]]:
    """Load the fleet placement. Missing/unparseable file → an empty mapping.

    A broken or absent placement must never keep the hub (or its reconcile
    loop) from starting — same tolerant-load contract as
    ``startup_profile.load_startup_profile``. Unlike that module, an absent live
    file returns an **empty** mapping (no example fallback — the reconcile loop
    must stay inert until placement is deliberately set); the committed template
    is copied into place, never auto-enforced. The cache keys on the resolved
    ``target`` so ``save_fleet_placement``'s invalidation lands on the same slot.
    """
    target = Path(path) if path else DEFAULT_PLACEMENT_PATH
    key = str(target)
    cached = _PLACEMENT_CACHE.get(key)
    if cached is not None:
        return cached

    if not target.exists():
        result: Dict[str, List[str]] = {}
    else:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("⚠️ could not load fleet placement %s: %s", target, exc)
            data = None
        result = _coerce(data)

    _PLACEMENT_CACHE[key] = result
    return result


def normalize_placement(data: Any) -> Dict[str, List[str]]:
    """Validate + clean a full placement payload for persistence.

    Unknown host ids raise ``ValueError`` (a structural mistake worth
    surfacing, unlike a stale model id). Per-host ids are filtered against that
    host's ``launchable_local_ids`` so a typo'd/removed model can never be
    persisted to silently no-op at reconcile time — mirroring
    ``startup_profile.normalize_profile``. Imported lazily to avoid a load-time
    import cycle (``model_registry`` reads config the same modules populate).
    """
    if not isinstance(data, dict):
        raise ValueError("fleet placement must be a JSON object")

    from src.host_profile import get_host
    from src.model_registry import launchable_local_ids

    clean: Dict[str, List[str]] = {}
    for raw_host, ids in data.items():
        host_id = str(raw_host)
        owner = get_host(host_id)
        if owner is None:
            raise ValueError(f"unknown host {host_id!r}")
        if not isinstance(ids, list):
            raise ValueError(f"placement for {host_id!r} must be a list")
        valid = set(launchable_local_ids(owner))
        # Order-preserving de-dup + drop of non-launchable ids.
        seen: set[str] = set()
        clean[host_id] = [
            m for m in (str(x) for x in ids if x)
            if m in valid and not (m in seen or seen.add(m))
        ]
    return clean


def save_fleet_placement(data: Any, path: Optional[str] = None) -> Dict[str, List[str]]:
    """Validate, atomically write, and invalidate the load cache."""
    target = Path(path) if path else DEFAULT_PLACEMENT_PATH
    clean = normalize_placement(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    _PLACEMENT_CACHE.pop(str(target), None)
    logger.info(
        "💾 Saved fleet placement (%d host(s): %s)",
        len(clean), {h: len(v) for h, v in clean.items()},
    )
    return clean
