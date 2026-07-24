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
from src import fleet_reconcile, remote_stats, services as svc
from src.host_profile import HostProfile, all_hosts, resolve as resolve_host
from src.model_registry import all_models, launchable_local_ids

logger = logging.getLogger(__name__)
router = APIRouter()

# Live-running badges come from a manageable peer's own hub models API. Bound it
# short so a peer that's powered on but whose hub is slow/absent doesn't stall an
# on-demand tab-open — the box's own TCP liveness (below) already settles online
# vs offline; the models call only enriches the badges.
_GRID_PROBE_TIMEOUT_S = 2.5


def _display_names() -> Dict[str, str]:
    return {m.id: m.display_name for m in all_models()}


def _vram_estimates() -> Dict[str, int]:
    """``{model_id: est_vram_mb}`` for every model that declares a footprint
    (#375). A row without ``est_vram_mb`` is absent — the capacity sum treats a
    missing id as 0, so subscription/virtual/CPU rows contribute nothing."""
    return {m.id: m.est_vram_mb for m in all_models() if m.est_vram_mb is not None}


def _device_hints() -> Dict[str, Dict[str, str]]:
    """``{host_id: {model_id: "cpu"}}`` — CPU residency is per *(model, host)*,
    so the grid can show a small 'cpu' hint per row (#387) — a model
    contributing 0 to the VRAM sum reads as *intentionally exempt*, not as an
    omission.

    Config-derived, no live probe. Two independent ways a row lands on CPU:

    * **Always, on every host** — piper's shim hardcodes CPU unconditionally
      (``src/tts_engines/piper.py``, #371) and a ``whisper-server`` row that
      *declares* ``-ng`` never touches the GPU (see ``whisper_translate``).
    * **On one host only** — a failover chain's degraded last-resort tier
      (``{id: tower, cpu: true}``, #342): GPU on the preferred members,
      CPU-offloaded on the flagged one.

    Reads ``all_models(apply_cpu_offload=False)`` deliberately: the registry
    bakes the CPU rewrite in for the *active* host, so the default view would
    show ``-ng`` on this box's row and smear that verdict across every other
    chain member (#405).

    Deliberately **not** ``est_vram_mb == 0`` alone — ``parakeet`` is also 0
    but runs on the Mac's ANE via CoreML, a real (if non-discrete-VRAM)
    device, not "cpu"; and the ``qwen35_4b_moe`` virtual alias shares its
    host row's GPU process. Display only (#387) — this never feeds the
    capacity sum, which already keys off ``est_vram_mb``.
    """
    hints: Dict[str, Dict[str, str]] = {h.id: {} for h in all_hosts()}
    for m in all_models(apply_cpu_offload=False):
        always_cpu = m.tts_engine == "piper" or (
            m.engine == "whisper-server" and "-ng" in m.args
        )
        for host_id, per_host in hints.items():
            if always_cpu or host_id in m.cpu_hosts:
                per_host[m.id] = "cpu"
    return hints


def _capacity(
    profile: HostProfile, placed: List[str], running: List[str], vram: Dict[str, int]
) -> Dict[str, Any]:
    """The host's VRAM headroom against its declared ceiling (#375).

    Sums ``est_vram_mb`` over the union of *placed* (desired) and *running*
    (live) model ids — a model can be either without the other, and both draw
    VRAM. The result is **advisory**: ``capacity_warning`` is True only when the
    host declares a ``vram_mb`` ceiling AND the estimate exceeds it. A host with
    no ceiling (Apple-silicon unified memory, managed-only boxes) never warns —
    ``vram_mb`` is None and the sum is reported for context only.
    """
    considered = list(dict.fromkeys([*placed, *running]))
    est = sum(vram.get(m, 0) for m in considered)
    ceiling = profile.vram_mb
    return {
        "vram_mb": ceiling,
        "est_vram_mb": est,
        "capacity_warning": ceiling is not None and est > ceiling,
    }


async def _host_status(
    profile: HostProfile,
    active_id: str,
    placement: Dict[str, List[str]],
    names: Dict[str, str],
    vram: Dict[str, int],
    devices: Dict[str, str],
) -> Dict[str, Any]:
    """One host's grid row: its placeable models, live status, capacity
    headroom, and whether the control plane can manage it.

    Reachability is the **hub-independent TCP liveness** the Machines tab uses
    (``remote_stats.is_reachable`` — *is the box powered on?*), not a hub
    ``/health`` probe, so a managed-only satellite that runs no hub (``gaming``,
    ``openclaw``) still reads "online" honestly. ``runs_hub`` (a host declares
    launchable models) is what the reconcile loop needs to *place* onto a peer;
    a host with none is shown but not placeable — a real state, spelled out in
    the UI rather than an offer that can't be honoured.
    """
    hid = profile.id
    eligible_ids = launchable_local_ids(profile)
    eligible_set = set(eligible_ids)
    eligible = [
        {"id": m, "display_name": names.get(m, m), "device": devices.get(m)}
        for m in eligible_ids
    ]
    placed = placement.get(hid, [])
    runs_hub = bool(eligible_ids)  # only a host with launchable models runs this hub

    base = {
        "id": hid, "display_name": profile.display_name or hid,
        "icon": profile.icon or ("monitor" if hid == active_id else "server"),
        "can_ssh": profile.can_ssh, "runs_hub": runs_hub,
        "eligible": eligible, "placed": placed,
    }

    if hid == active_id:
        # Only the placeable (eligible) models that are up — so a grid cell reads
        # "placed ✓ running / ✗ down". Excludes subscription + virtual rows.
        running = [m for m in bp.running_backends().keys() if m in eligible_set]
        return {
            **base, "local": True, "reachable": True, "dormant": False,
            "running": running, **_capacity(profile, placed, running, vram),
        }

    # A peer: liveness by TCP connect (is the box on?), independent of whether it
    # runs a hub. A dormant node is never live-probed (it's declared powered down).
    reachable = False if profile.dormant else await remote_stats.is_reachable(profile)
    running: List[str] = []
    if reachable and runs_hub:
        # Only a hub-running peer exposes a models API for live running badges.
        rows = await svc.remote_models(profile, timeout_s=_GRID_PROBE_TIMEOUT_S) or []
        running = [
            r["id"] for r in rows
            if isinstance(r, dict) and r.get("id") in eligible_set and r.get("reachable")
        ]
    return {
        **base, "local": False, "reachable": reachable,
        "dormant": profile.dormant, "running": running,
        **_capacity(profile, placed, running, vram),
    }


@router.get("/api/fleet-placement")
async def get_fleet_placement() -> Dict[str, Any]:
    """Desired placement + a row for **every** fleet host: its placeable models,
    live liveness, and whether it's manageable from here."""
    placement = fp.load_fleet_placement()
    active_id = resolve_host().id
    names = _display_names()
    vram = _vram_estimates()
    devices = _device_hints()
    statuses = await asyncio.gather(
        *(
            _host_status(h, active_id, placement, names, vram, devices.get(h.id, {}))
            for h in all_hosts()
        )
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
