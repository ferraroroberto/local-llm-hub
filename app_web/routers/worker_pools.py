"""Worker pool sizes — Settings › Performance › Worker pools.

  * ``GET /api/worker-pools`` — per pool: what it runs, its default, the saved
    size and the size the running hub was built with.
  * ``PUT /api/worker-pools`` — ``{"sizes": {"<pool>": <int>, ...}}``; refuses a
    value that is not a whole number in range (400, with the reason).

The executors are sized when the hub starts (``src/concurrency/pools.py``), so
a save applies at the next restart; ``restart_pending`` says when the saved
sizes differ from the running ones.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from src.concurrency import pool_sizes
from src.concurrency.pools import ACTION, RUNNING_SIZES, run_on

from ._helpers import maybe_json

router = APIRouter()


def _payload() -> Dict[str, Any]:
    saved = pool_sizes.effective_sizes()
    return {
        "min": pool_sizes.MIN_SIZE,
        "max": pool_sizes.MAX_SIZE,
        "restart_pending": saved != RUNNING_SIZES,
        "pools": [
            {
                "id": spec.id,
                "label": spec.label,
                "description": spec.description,
                "default": spec.default,
                "size": saved[spec.id],
                "running": RUNNING_SIZES[spec.id],
            }
            for spec in pool_sizes.SPECS
        ],
    }


@router.get("/api/worker-pools")
async def worker_pools() -> Dict[str, Any]:
    return await run_on(ACTION, _payload)


@router.put("/api/worker-pools")
async def worker_pools_save(request: Request) -> Dict[str, Any]:
    body = await maybe_json(request)
    sizes = body.get("sizes")
    if not isinstance(sizes, dict):
        raise HTTPException(status_code=400, detail='body must be {"sizes": {"<pool>": <size>, ...}}')
    try:
        await run_on(ACTION, pool_sizes.save, sizes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **await run_on(ACTION, _payload)}
