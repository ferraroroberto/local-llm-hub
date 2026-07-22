"""Fleet placement API — the tower's fleet-wide desired-state control surface.

Step 2 of the always-on control plane (#353). Backs the (Step 3) placement grid:
``config/fleet_placement.json`` — owned by ``src.fleet_placement`` — maps each
host to the models that *should* run on it, and a background reconcile loop
(``src.fleet_reconcile``) enforces it. This router is a thin CRUD shell over that
file plus the per-host status the grid renders (eligible models, reachability,
what's actually running).

  * ``GET   /api/fleet-placement`` → desired placement + per-host status.
  * ``PATCH /api/fleet-placement`` → merge a partial ``{host: [ids]}`` update,
    persist it, and apply the delta now (stop un-placed, start newly-placed).
  * ``POST  /api/fleet-placement/reconcile`` → run one additive convergence pass
    on demand (the loop already does this on boot + every few minutes).

Local to the tower (the control node) — placement isn't host-addressed, unlike
the startup profile: one machine holds the fleet's intent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from src import backend_process as bp
from src import fleet_placement as fp
from src import fleet_reconcile, services as svc
from src.host_profile import HostProfile, all_hosts, resolve as resolve_host
from src.model_registry import all_models, launchable_local_ids

logger = logging.getLogger(__name__)
router = APIRouter()


def _display_names() -> Dict[str, str]:
    return {m.id: m.display_name for m in all_models()}


def _placeable_hosts(placement: Dict[str, List[str]]) -> List[HostProfile]:
    """Hosts worth showing in the grid: any that can launch at least one model,
    plus any that already carries a placement (so a placement made against a
    now-empty host still renders rather than silently vanishing)."""
    hosts = [h for h in all_hosts() if launchable_local_ids(h) or placement.get(h.id)]
    return hosts


async def _host_status(
    profile: HostProfile, active_id: str, placement: Dict[str, List[str]], names: Dict[str, str]
) -> Dict[str, Any]:
    hid = profile.id
    eligible_ids = launchable_local_ids(profile)
    eligible_set = set(eligible_ids)
    eligible = [{"id": m, "display_name": names.get(m, m)} for m in eligible_ids]
    placed = placement.get(hid, [])

    if hid == active_id:
        # Only the placeable (eligible) models that are up — so a grid cell reads
        # "placed ✓ running / ✗ down". Excludes subscription + virtual rows.
        running = [m for m in bp.running_backends().keys() if m in eligible_set]
        return {
            "id": hid, "display_name": profile.display_name or hid,
            "local": True, "reachable": True, "can_ssh": profile.can_ssh,
            "eligible": eligible, "placed": placed, "running": running,
        }

    health = await svc.mac_mini_health(hid)  # generic peer /health + version probe
    reachable = bool(health.get("reachable"))
    running: List[str] = []
    if reachable:
        rows = await svc.remote_models(profile) or []
        running = [
            r["id"] for r in rows
            if isinstance(r, dict) and r.get("id") in eligible_set and r.get("reachable")
        ]
    return {
        "id": hid, "display_name": profile.display_name or hid,
        "local": False, "reachable": reachable, "can_ssh": profile.can_ssh,
        "git_sha_match": health.get("git_sha_match"),
        "eligible": eligible, "placed": placed, "running": running,
    }


@router.get("/api/fleet-placement")
async def get_fleet_placement() -> Dict[str, Any]:
    """Desired placement + live per-host status (eligible / reachable / running)."""
    placement = fp.load_fleet_placement()
    active_id = resolve_host().id
    names = _display_names()
    hosts = _placeable_hosts(placement)
    statuses = await asyncio.gather(
        *(_host_status(h, active_id, placement, names) for h in hosts)
    )
    return {"placement": placement, "hosts": list(statuses)}


@router.patch("/api/fleet-placement")
async def patch_fleet_placement(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a partial ``{host_id: [model_id, ...]}`` update and apply it now.

    Merge is per-host (a host present in the body replaces that host's list,
    others untouched) — mirroring the startup profile's partial PATCH. After
    persisting the validated placement, each touched host's delta is applied
    immediately: un-placed models are stopped + de-profiled, newly-placed models
    are started (waking an offline satellite if needed).
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    old = fp.load_fleet_placement()
    merged = {**old, **payload}
    try:
        clean = fp.save_fleet_placement(merged)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    active_id = resolve_host().id
    applied: Dict[str, Any] = {}
    for host_id in payload:
        hid = str(host_id)
        applied[hid] = await fleet_reconcile.apply_placement_change(
            hid, old.get(hid, []), clean.get(hid, []), active_id
        )
    return {"ok": True, "placement": clean, "applied": applied}


@router.post("/api/fleet-placement/reconcile")
async def reconcile_now() -> Dict[str, Any]:
    """Run one additive convergence pass on demand (same as the periodic loop)."""
    return {"ok": True, "results": await fleet_reconcile.reconcile_once()}
