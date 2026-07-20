"""On-demand machine diagnostics API (#315) — behind the Machines tab's
diagnostics modal.

  * ``GET  /admin/api/diagnostics/status``       — is a capture running, plus settings
  * ``POST /admin/api/diagnostics/start``        — begin a timed capture
  * ``POST /admin/api/diagnostics/snapshot``     — one-shot sample
  * ``POST /admin/api/diagnostics/stop``         — stop the active capture
  * ``POST /admin/api/diagnostics/ingest``       — ingest a portable foreign capture (#316)
  * ``GET  /admin/api/diagnostics/runs``         — past runs, newest first
  * ``GET  /admin/api/diagnostics/runs/{id}``    — the summary digest
  * ``GET  /admin/api/diagnostics/runs/{id}/drift``  — delta vs the baseline
  * ``GET  /admin/api/diagnostics/runs/{id}/report``  — LLM-ready markdown
  * ``GET  /admin/api/diagnostics/runs/{id}/export``  — raw JSON for mining
  * ``POST /admin/api/diagnostics/runs/{id}/baseline`` — mark as baseline
  * ``DELETE /admin/api/diagnostics/runs/{id}``  — drop a run and its rows
  * ``PUT  /admin/api/diagnostics/settings``     — retention + scheduled snapshot

Reads ride the loopback-bypass middleware like the other admin reads. Start /
stop / delete change real state, so they ride the normal auth — the same
stance ``machines.py`` takes for its power actions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import PlainTextResponse

from src.diagnostics import ingest, report, rules, sampler, settings as diag_settings, store

from ._helpers import maybe_json

logger = logging.getLogger(__name__)
router = APIRouter()


def _settings_payload() -> Dict[str, Any]:
    cfg = diag_settings.load_settings()
    return {
        **cfg.as_dict(),
        "scheduled_active": sampler.scheduled_active(),
        "db_size_bytes": store.db_size_bytes(),
    }


@router.get("/api/diagnostics/status")
async def diagnostics_status() -> Dict[str, Any]:
    """Live capture progress (or ``null``) plus the current settings."""
    active = sampler.active_run()
    return {
        "capturing": active is not None,
        "active": active.as_dict() if active else None,
        "settings": _settings_payload(),
        "limits": {
            "min_interval_s": sampler.MIN_INTERVAL_S,
            "max_interval_s": sampler.MAX_INTERVAL_S,
            "max_duration_s": sampler.MAX_DURATION_S,
        },
    }


@router.post("/api/diagnostics/start")
async def diagnostics_start(request: Request) -> Dict[str, Any]:
    body = await maybe_json(request)
    cfg = diag_settings.load_settings()
    try:
        interval = float(body.get("interval_s", sampler.DEFAULT_INTERVAL_S))
        raw_duration = body.get("duration_s", sampler.DEFAULT_DURATION_S)
        duration = None if raw_duration is None else float(raw_duration)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="interval_s and duration_s must be numbers")

    try:
        active = await sampler.start_run(
            interval_s=interval, duration_s=duration,
            trigger="manual", retention_days=cfg.retention_days,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    logger.info("🔬 /admin/api/diagnostics/start -> %s", active["run_id"])
    return {"ok": True, "active": active}


@router.post("/api/diagnostics/snapshot")
async def diagnostics_snapshot() -> Dict[str, Any]:
    cfg = diag_settings.load_settings()
    try:
        result = await sampler.one_shot(retention_days=cfg.retention_days)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ diagnostics snapshot failed: %s", exc)
        raise HTTPException(status_code=500, detail="snapshot failed")
    logger.info("🔬 /admin/api/diagnostics/snapshot -> %s", result["run_id"])
    return {"ok": True, **result}


@router.post("/api/diagnostics/stop")
async def diagnostics_stop() -> Dict[str, Any]:
    result = await sampler.stop_run()
    logger.info("🔬 /admin/api/diagnostics/stop -> %s", result)
    return {"ok": True, **result}


@router.post("/api/diagnostics/ingest")
async def diagnostics_ingest(request: Request) -> Dict[str, Any]:
    """Ingest a portable capture (``scripts/portable_capture.py`` output) from a
    hub-less machine as an ordinary run — the native-path symmetry for the SSH
    delivery (#316). The heavy lifting (attribution, coverage, verdict) runs in
    ``ingest.ingest_payload``; this just carries the JSON in and the run id out."""
    body = await maybe_json(request)
    if isinstance(body, dict) and isinstance(body.get("payload"), dict):
        payload, machine = body["payload"], body.get("machine")
    else:
        payload, machine = body, None
    try:
        run_id = await asyncio.to_thread(ingest.ingest_payload, payload, machine=machine)
    except ingest.IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ diagnostics ingest failed: %s", exc)
        raise HTTPException(status_code=500, detail="ingest failed")
    logger.info("📥 /admin/api/diagnostics/ingest -> %s", run_id)
    return {"ok": True, "run_id": run_id}


@router.get("/api/diagnostics/runs")
async def diagnostics_runs(limit: int = 50) -> Dict[str, Any]:
    runs = await asyncio.to_thread(store.list_runs, max(1, min(500, limit)))
    return {"runs": runs}


@router.get("/api/diagnostics/runs/{run_id}")
async def diagnostics_run_summary(run_id: str) -> Dict[str, Any]:
    data = await asyncio.to_thread(report.summary, run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return data


@router.get("/api/diagnostics/runs/{run_id}/drift")
async def diagnostics_run_drift(run_id: str) -> Dict[str, Any]:
    data = await asyncio.to_thread(report.drift, run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return data


@router.get("/api/diagnostics/runs/{run_id}/report", response_class=PlainTextResponse)
async def diagnostics_run_report(run_id: str) -> PlainTextResponse:
    """The markdown health report — served as a download so it can be pasted
    into an LLM session or kept alongside other artefacts."""
    text = await asyncio.to_thread(report.markdown_report, run_id)
    if text is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return PlainTextResponse(
        text, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="diagnostics-{run_id}.md"'},
    )


@router.get("/api/diagnostics/runs/{run_id}/export")
async def diagnostics_run_export(run_id: str) -> Dict[str, Any]:
    """Every stored row for one run — the offline-mining payload.

    JSON rather than the raw ``.db`` on purpose: a single run exports as a
    self-describing document, while shipping the whole database would hand
    over every *other* run on the machine as well."""
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    samples = await asyncio.to_thread(store.samples, run_id)
    processes = await asyncio.to_thread(store.process_aggregates, run_id)
    apps = await asyncio.to_thread(store.app_aggregates, run_id)
    ports = await asyncio.to_thread(store.listening_ports, run_id)
    timeline = await asyncio.to_thread(store.process_count_timeline, run_id)
    return {
        "run": run, "samples": samples, "process_aggregates": processes,
        "app_aggregates": apps, "ports": ports, "process_timeline": timeline,
    }


@router.post("/api/diagnostics/runs/{run_id}/baseline")
async def diagnostics_set_baseline(run_id: str) -> Dict[str, Any]:
    try:
        await asyncio.to_thread(store.set_baseline, run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown run")
    logger.info("📌 diagnostics baseline set to %s", run_id)
    return {"ok": True, "run_id": run_id}


@router.post("/api/diagnostics/runs/{run_id}/evaluate")
async def diagnostics_reevaluate(run_id: str) -> Dict[str, Any]:
    """Re-run the verdict against current thresholds — the point of keeping
    rules in a config file is being able to retune and re-judge old runs."""
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    await asyncio.to_thread(rules.reload_thresholds)
    result = await asyncio.to_thread(rules.evaluate_and_save, run_id)
    return {"ok": True, **result}


@router.delete("/api/diagnostics/runs/{run_id}")
async def diagnostics_delete_run(run_id: str) -> Dict[str, Any]:
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    await asyncio.to_thread(store.delete_run, run_id)
    logger.info("🗑️ diagnostics run deleted: %s", run_id)
    return {"ok": True}


@router.put("/api/diagnostics/settings")
async def diagnostics_save_settings(request: Request) -> Dict[str, Any]:
    body = await maybe_json(request)
    try:
        clean = await asyncio.to_thread(diag_settings.save_settings, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Apply the schedule change immediately — a toggle the user has to
    # restart the hub to activate is a toggle that lies.
    if clean.scheduled_enabled:
        await sampler.start_scheduled_snapshots(
            clean.scheduled_interval_hours, retention_days=clean.retention_days,
        )
    else:
        await sampler.stop_scheduled_snapshots()
    return {"ok": True, "settings": _settings_payload()}
