"""Models tab API — per-backend tile state + start/stop/force-stop/ping."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from src import backend_process as bp
from src.model_registry import Model, enabled_models, resolve as resolve_model
from src.server_process import (
    OWNERSHIP_EXTERNAL,
    OWNERSHIP_NONE,
    OWNERSHIP_OURS,
    snapshot_listening_pids,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _ownership_from_snapshot(m: Model, listening: Dict[int, list]) -> tuple[str, Any]:
    """Compute (ownership, pid) for a controllable model from a port→pids map.

    Avoids the per-model netstat invocation that ``bp.ownership`` does
    when checking each model in isolation.
    """
    if bp.is_running(m.id):
        return OWNERSHIP_OURS, bp.pid(m.id)
    if not m.port:
        return OWNERSHIP_NONE, None
    pids = listening.get(m.port) or []
    if pids:
        return OWNERSHIP_EXTERNAL, pids[0]
    return OWNERSHIP_NONE, None


@router.get("/api/models")
async def list_models_for_admin() -> Dict[str, Any]:
    """Per-tile state for every enabled model.

    Two pieces are expensive: probing reachability over HTTP per backend
    (each costs up to 0.5 s) and resolving port → PID via netstat (one
    shell-out per call). We fan the HTTP probes out concurrently, and
    do a single netstat snapshot up front instead of one per backend —
    O(N) → O(1) subprocesses, O(N) → O(0.5 s) wall time when all
    backends are alive.
    """
    models = list(enabled_models())
    # psutil gives us every listening port in ~2 ms — use it both for
    # ownership *and* as a cheap reachability gate so we never fire an
    # HTTP probe at a port that isn't bound.
    listening = await asyncio.to_thread(snapshot_listening_pids)

    async def _probe_reach(m: Model) -> bool:
        if m.backend == "claude" or m.backend == "gemini":
            # Subscription-backed — always "live" if the hub itself
            # answered, which the caller already knows it did.
            return True
        if not m.port or m.port not in listening:
            # Port isn't bound → definitely not reachable; skip the
            # 1-second-per-dead-backend HTTP probe.
            return False
        return await asyncio.to_thread(bp.is_reachable, m, 0.4)

    reach_results = await asyncio.gather(*(_probe_reach(m) for m in models))

    rows: List[Dict[str, Any]] = []
    for m, reachable in zip(models, reach_results):
        controllable = m.backend in ("openai", "whisper")
        own = OWNERSHIP_NONE
        pid: Any = None
        if controllable:
            own, pid = _ownership_from_snapshot(m, listening)
        rows.append(
            {
                "id": m.id,
                "display_name": m.display_name,
                "backend": m.backend,
                "engine": m.engine,
                "port": m.port,
                "url": m.url,
                "aliases": list(m.aliases or []),
                "controllable": controllable,
                "ownership": own,
                "pid": pid,
                "reachable": bool(reachable),
                "model_path": m.model_path,
            }
        )
    return {"models": rows}


@router.post("/api/models/{model_id}/start")
async def model_start(model_id: str) -> Dict[str, Any]:
    target = bp.resolve_model_by_id(model_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"model {model_id!r} not enabled")
    if not (target.backend in ("openai", "whisper")):
        raise HTTPException(
            status_code=400,
            detail=f"backend {target.backend!r} has no managed process (subscription-backed)",
        )
    ok, msg = bp.start(model_id)
    if not ok:
        # "already running" is OK in the SPA — surface as 409 so the UI can ignore.
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "detail": msg}


@router.post("/api/models/{model_id}/stop")
async def model_stop(model_id: str) -> Dict[str, Any]:
    target = bp.resolve_model_by_id(model_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"model {model_id!r} not enabled")
    ok, msg = bp.stop(model_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "detail": msg}


@router.post("/api/models/{model_id}/force-stop")
async def model_force_stop(model_id: str) -> Dict[str, Any]:
    """Force-kill whatever process holds this model's port.

    Use when the hub doesn't own the process — e.g. a stale backend
    from a previous tray session, or a llama-server someone started
    by hand. taskkill on Windows, SIGKILL on POSIX. The hub doesn't
    know what's listening — that's the whole point — so the caller
    is implicitly saying "I take responsibility for this PID".
    """
    target = bp.resolve_model_by_id(model_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"model {model_id!r} not enabled")
    ok, msg = bp.force_stop_external(model_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "detail": msg}


@router.post("/api/models/{model_id}/ping")
async def model_ping(model_id: str) -> Dict[str, Any]:
    """Send a 1-token test prompt through the hub and report latency + tokens.

    Useful to confirm the backend actually answers, not just that the port
    is open. For subscription-backed claude/gemini rows the alias resolves
    inside the hub the same way as any other request.
    """
    target = bp.resolve_model_by_id(model_id)
    if target is None:
        # Could still be a claude/gemini row — those aren't backed by
        # backend_process but are still resolvable in the registry.
        target = resolve_model(model_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"unknown model {model_id!r}")

    import httpx
    from src.host_profile import hub_port

    url = f"http://127.0.0.1:{hub_port()}/v1/messages"
    payload = {
        "model": target.display_name,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    t0 = time.monotonic_ns()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "status": 0,
            "latency_ms": (time.monotonic_ns() - t0) / 1e6,
            "error": str(exc),
        }
    latency_ms = (time.monotonic_ns() - t0) / 1e6
    body: Dict[str, Any] = {}
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {"raw": r.text[:300]}
    usage = body.get("usage") if isinstance(body, dict) else None
    return {
        "ok": r.is_success,
        "status": r.status_code,
        "latency_ms": round(latency_ms, 1),
        "usage": usage or {},
        "error": "" if r.is_success else (body.get("detail") if isinstance(body, dict) else str(r.status_code)),
    }
