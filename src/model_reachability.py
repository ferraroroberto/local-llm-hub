"""Is each enabled model answering? — shared by the Models and Playground tabs.

``app_web/routers/models.py`` builds the full per-tile listing; the Playground
tab only needs the ``reachable`` bit for its TTS rows. Both used to get it from
the Models handler (the Playground router imported it, breaking the "no router
imports another router" rule in ``app_web/routers/_helpers.py``, and paid for
the whole listing — device probes, the Jev tile, the config block — to read one
field). The probe lives here so both call it directly.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence

from . import backend_process as bp
from . import remote_stats
from .host_profile import get_host, resolve as resolve_host
from .model_failover import effective_owner
from .model_registry import Model
from .server_process import snapshot_listening_pids

# TCP-level probe budget per backend, seconds: short because a dead backend
# is the common case on a box that only runs a subset of the registry.
_PROBE_TIMEOUT_S = 0.4


async def local_reachability(
    models: Sequence[Model], listening: Dict[int, Any]
) -> List[bool]:
    """Reachability of host-local ``models``, in order.

    ``listening`` is a port → PIDs snapshot (``snapshot_listening_pids``): a
    port that isn't bound is reported down without an HTTP probe, which would
    otherwise cost a timeout per dead backend. Subscription-backed rows
    (claude/gemini) are always "live" when the hub itself answered.
    """

    async def _probe(m: Model) -> bool:
        if m.backend in ("claude", "gemini"):
            return True
        if not m.port or m.port not in listening:
            return False
        return await asyncio.to_thread(bp.is_reachable, m, _PROBE_TIMEOUT_S)

    return list(await asyncio.gather(*(_probe(m) for m in models)))


async def reachability_by_id(models: Sequence[Model]) -> Dict[str, bool]:
    """``{model id: reachable}`` for ``models`` wherever they are served.

    Rows this host currently owns are probed locally; rows owned by a peer take
    the owner's own answer (this hub can't see another machine's ports), and
    read as down when the owner can't be reached.
    """
    active = resolve_host()
    owner_by_id = {m.id: effective_owner(m) for m in models}
    local = [m for m in models if owner_by_id[m.id] in (None, active.id)]
    remote = [m for m in models if owner_by_id[m.id] not in (None, active.id)]

    out: Dict[str, bool] = {}
    if local:
        listening = await asyncio.to_thread(snapshot_listening_pids)
        for m, ok in zip(local, await local_reachability(local, listening)):
            out[m.id] = bool(ok)

    owners: Dict[str, List[Model]] = {}
    for m in remote:
        owners.setdefault(owner_by_id[m.id], []).append(m)
    for host_id, owned in owners.items():
        profile = get_host(host_id)
        fetched = await remote_stats.remote_models(profile) if profile else None
        by_id: Optional[Dict[Any, Any]] = (
            {r.get("id"): r for r in fetched if isinstance(r, dict)}
            if fetched is not None
            else None
        )
        for m in owned:
            row = by_id.get(m.id) if by_id is not None else None
            out[m.id] = bool(row and row.get("reachable"))
    return out
