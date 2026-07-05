"""FastAPI startup/shutdown lifecycle wiring + the background resource
sampler for the hub app.

Extracted out of ``server.py`` (issue #198) — that module's own docstring
already flagged ``/v1/images/*`` and ``/v1/audio/*`` as prior splits for
exactly this reason (keeping the god-module from growing further); the
startup/shutdown event bodies and the 2s-tick resource sampler were the
next-largest chunk still living inline, mixed in with route registration.

``server.py`` wires these on with ``app.add_event_handler(...)`` (the
non-decorator form of ``@app.on_event``) rather than decorating them here,
so the functions stay plain, directly-callable, and directly-testable —
``tests/test_restart_keepalive.py`` calls ``stop_backend_children()``
straight, with no FastAPI app in the loop.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .hub_log import HUB_LOG
from .hub_observability import OBS

logger = logging.getLogger(__name__)


async def stop_backend_children() -> None:
    """Tear down every model subprocess the hub spawned.

    The hub owns its backend children (since the tray drives them via
    the admin API). Without this, a clean ``CTRL+C`` would leave
    orphan ``llama-server`` / ``whisper-server`` processes holding
    their ports until the user logged out.

    Exception: on an admin **restart** the children must survive so the
    respawned hub re-adopts them (``inherit_running_backends``). The
    restart endpoint sets ``backend_process.restart_pending()`` before
    signalling shutdown; we honour it by skipping teardown.
    """
    from . import backend_process as bp
    from . import http_client

    try:
        await http_client.aclose()
        http_client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("shutdown: closing shared httpx clients raised: %s", exc)

    if bp.restart_pending():
        survivors = list(bp.running_backends().keys())
        logger.info(
            "shutdown: restart in progress — leaving %d backend(s) running for adoption: %s",
            len(survivors), survivors,
        )
        return

    for model_id in list(bp.running_backends().keys()):
        try:
            ok, msg = bp.stop(model_id)
            logger.info("shutdown: stop %s -> %s %s", model_id, ok, msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("shutdown: stop %s raised: %s", model_id, exc)


async def wire_observatory_loop() -> None:
    """Capture the running event loop so the synchronous middleware can
    fan out SSE events from non-async callers."""
    loop = asyncio.get_running_loop()
    OBS.attach_loop(loop)
    HUB_LOG.attach_loop(loop)
    # Start the resource sampler. 2s tick × 150 samples = 5 min ring.
    loop.create_task(_resource_sampler())

    # Inherit any backend process left running on one of our ports by a
    # previous hub instance. Without this, every hub restart shows the
    # surviving model backends as "adopted" rather than "running".
    try:
        from . import backend_process as bp
        inherited = await asyncio.to_thread(bp.inherit_running_backends)
        if inherited:
            logger.info("📎 Inherited %d running backend(s) from a previous hub", inherited)
    except Exception as exc:  # noqa: BLE001
        logger.warning("inherit_running_backends failed: %s", exc)

    # The hub owns configured backend autostart so every launch surface
    # (tray, run_hub.bat, python -m src.run_backend hub) behaves the same.
    loop.create_task(_autostart_configured_backends())


async def _autostart_configured_backends() -> None:
    from . import backend_process as bp
    from .model_registry import autostart_model_ids

    model_ids = autostart_model_ids()
    if not model_ids:
        return
    logger.info("autostart: configured backend set: %s", model_ids)
    for model_id in model_ids:
        try:
            ok, msg = await asyncio.to_thread(bp.start, model_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("autostart: %s raised: %s", model_id, exc)
            continue
        if ok or "already running" in msg.lower():
            logger.info("autostart: %s -> %s", model_id, msg)
        else:
            logger.warning("autostart: %s -> %s", model_id, msg)


async def _resource_sampler() -> None:
    """Background task that samples RAM + GPU usage every 2 s."""
    from . import system_stats
    from .hub_observability import StatSample

    while True:
        try:
            ram = system_stats.ram_stats()
            gpus = system_stats.gpu_stats()
            gpu0_vram = None
            gpu0_util = None
            if gpus:
                first = gpus[0]
                gpu0_vram = first.get("vram_percent")
                gpu0_util = first.get("util_percent")
            OBS.record_stat(
                StatSample(
                    ts=time.time(),
                    ram_percent=float(ram.get("percent", 0.0)),
                    gpu0_vram_percent=gpu0_vram,
                    gpu0_util_percent=gpu0_util,
                )
            )
        except Exception:  # noqa: BLE001 — sampler must not die
            pass
        await asyncio.sleep(2.0)


def register(app) -> None:
    """Attach the hub's startup/shutdown handlers to ``app``.

    Calls ``app.on_event(event_type)`` as a plain function (its decorator
    return value) rather than using ``@app.on_event(...)`` sugar directly
    on these functions, so the handlers themselves stay plain module-level
    callables — importable and directly testable without needing a
    FastAPI app (``tests/test_restart_keepalive.py`` calls
    ``stop_backend_children()`` straight). Starlette's ``Router`` dropped
    ``add_event_handler`` in favor of lifespan context managers; ``on_event``
    is FastAPI's still-supported (if deprecated) escape hatch for the
    decorator-less registration this module needs.
    """
    app.on_event("shutdown")(stop_backend_children)
    app.on_event("startup")(wire_observatory_loop)
