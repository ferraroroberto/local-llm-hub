"""Telemetry tab API — Langfuse health, trace feed, per-model leaderboard,
feedback endpoint. Mounts under ``/admin/api/telemetry`` plus a sibling
``/admin/api/trace/{id}/feedback`` for client-side score posting.

Reuses the in-memory ``OBS`` ring (already populated by every routed
request) for the live trace feed and the per-model leaderboard rather
than re-querying Langfuse on every poll — Langfuse is the durable
store, OBS is the cheap "what's happening right now" surface. The two
agree because each routed request stashes the OTel trace_id onto the
OBS record (see ``_stash_trace_id_on_ctx`` in ``src/server.py``).

The feedback endpoint wraps the Langfuse SDK's ``score()`` call. The
hub itself does not block on Langfuse — score uploads run as a
background task so the client gets a 202 in <50 ms.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, conint

from src.claude_code_otel import get_usage_summary as _claude_code_otel_summary
from src.hub_observability import OBS, _rec_to_dict
from src.observability import (
    hash_prompts_enabled,
    is_sdk_disabled,
    langfuse_basic_auth,
    langfuse_host as _langfuse_host_url,
    service_instance_id,
)

from ._helpers import sse_stream

logger = logging.getLogger(__name__)
router = APIRouter()

_HEALTH_TIMEOUT_S = 2.0
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Cached project_id — Langfuse trace URLs are of the form
# /project/{id}/traces/{trace_id} and the SPA needs the id to build
# clickable deep-links. We resolve it lazily on the first /health probe
# that finds Langfuse reachable, then keep it for the process lifetime.
# Set to "" when keys are missing or the lookup failed; we retry next health probe.
_PROJECT_ID_LOCK = threading.Lock()
_CACHED_PROJECT_ID: Optional[str] = None


def _langfuse_host() -> str:
    return _langfuse_host_url()


async def _resolve_project_id(client: httpx.AsyncClient) -> str:
    """Hit Langfuse's /api/public/projects to find the project tied to
    the current key pair. Caches the result process-wide.

    Returns "" on any failure — caller decides whether to retry.
    """
    global _CACHED_PROJECT_ID
    with _PROJECT_ID_LOCK:
        if _CACHED_PROJECT_ID:
            return _CACHED_PROJECT_ID

    auth = langfuse_basic_auth()
    if not auth:
        return ""
    try:
        r = await client.get(
            f"{_langfuse_host()}/api/public/projects",
            headers={"Authorization": auth},
        )
        if r.status_code != 200:
            return ""
        body = r.json()
        # Response shape: {"data": [{"id": "...", "name": "..."}, ...]}
        items = body.get("data") if isinstance(body, dict) else None
        if not items:
            return ""
        pid = (items[0] or {}).get("id") or ""
        if pid:
            with _PROJECT_ID_LOCK:
                _CACHED_PROJECT_ID = pid
        return pid
    except Exception:  # noqa: BLE001
        return ""


def _reset_project_id_cache_for_tests() -> None:
    """Tests-only — wipe the cached project_id."""
    global _CACHED_PROJECT_ID
    with _PROJECT_ID_LOCK:
        _CACHED_PROJECT_ID = None


# ---------------------------------------------------------------- health


@router.get("/api/telemetry/health")
async def telemetry_health() -> Dict[str, Any]:
    """Quick stack-health probe for the SPA's status strip.

    Reports: whether the OTel SDK is enabled in this hub process,
    whether Langfuse is reachable, the prompt-capture mode, the
    OTLP endpoint, the service.instance.id Langfuse uses to
    distinguish hosts, the Langfuse host URL, whether the API keys
    are configured, and (when reachable + authed) the resolved
    project_id so the SPA can build deep-link URLs.
    """
    sdk_disabled = is_sdk_disabled()
    otel_endpoint = _langfuse_host() + "/api/public/otel/v1/traces"
    auth_configured = langfuse_basic_auth() is not None
    langfuse_reachable = False
    langfuse_error = ""
    project_id = ""
    if not sdk_disabled:
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
                r = await client.get(f"{_langfuse_host()}/api/public/health")
                langfuse_reachable = r.status_code < 500
                if langfuse_reachable and auth_configured:
                    project_id = await _resolve_project_id(client)
        except Exception as exc:  # noqa: BLE001
            langfuse_error = f"{type(exc).__name__}: {exc}"

    return {
        "otel_enabled": not sdk_disabled,
        "otel_endpoint": otel_endpoint,
        "hash_prompts": hash_prompts_enabled(),
        # Internal host the hub uses to reach Langfuse (server -> Langfuse).
        # Always localhost-ish on the same machine.
        "langfuse_host": _langfuse_host(),
        # Client-facing deep-link wiring. The SPA builds the Langfuse UI
        # URL from window.location.hostname + langfuse_port — so opening
        # the admin from Tailscale / LAN / localhost transparently sends
        # the deep-link to the matching host. For Cloudflare-tunnel-style
        # access (where the hub's hostname can't be re-used for :3000),
        # set LANGFUSE_PUBLIC_URL in .env to point at the tunneled
        # Langfuse endpoint and the SPA uses that verbatim.
        "langfuse_port": 3000,
        "langfuse_public_url": (
            os.environ.get("LANGFUSE_PUBLIC_URL", "") or ""
        ).rstrip("/"),
        "langfuse_reachable": langfuse_reachable,
        "langfuse_error": langfuse_error,
        "langfuse_auth_configured": auth_configured,
        "langfuse_project_id": project_id,
        "service_instance_id": service_instance_id(),
    }


# ---------------------------------------------------------------- recent traces


@router.get("/api/telemetry/recent")
async def telemetry_recent(limit: int = 50) -> Dict[str, Any]:
    """Recent routed requests with their OTel trace IDs.

    Reads the in-memory ``OBS`` ring rather than Langfuse: this is the
    same source the Hub tab's live list uses, so the two tabs agree to
    the millisecond. Each record carries ``trace_id`` (empty when OTel
    is disabled) the SPA renders as a clickable Langfuse deep-link.
    """
    bounded = max(1, min(int(limit), 200))
    return {"traces": OBS.recent_requests(limit=bounded)}


# ---------------------------------------------------------------- live stream


@router.get("/api/telemetry/stream")
async def telemetry_stream(request: Request) -> StreamingResponse:
    """SSE stream of new routed requests. Same fan-out as the Hub tab's
    ``/api/hub/requests/stream`` but exposed under the telemetry prefix
    so the Telemetry tab's UI code is self-contained."""
    return sse_stream(
        request, OBS.subscribe, OBS.unsubscribe,
        seed=OBS.recent_requests(limit=20),
        to_dict=_rec_to_dict,
        reverse_seed=True,
    )


# ---------------------------------------------------------------- metrics / leaderboard


@router.get("/api/telemetry/metrics")
async def telemetry_metrics() -> Dict[str, Any]:
    """Per-model leaderboard from OBS counters + a couple of summary stats.

    OBS counters reset on hub restart (volatile). The Telemetry tab
    surfaces a "Reset at <uptime>" muted line so the user knows the
    window. For longer-window aggregates the user opens Langfuse via
    the deep link.
    """
    counters = OBS.counters_snapshot()
    started = OBS.started_at()
    requests_count = sum(c.get("requests", 0) for c in counters)
    errors_count = sum(c.get("errors", 0) for c in counters)
    err_rate = (errors_count / requests_count) if requests_count else 0.0
    return {
        "counters": counters,
        "summary": {
            "requests": requests_count,
            "errors": errors_count,
            "error_rate": round(err_rate, 4),
            "since_ts": started,
            "since_uptime_s": round(max(0.0, time.time() - started), 1),
        },
    }


# --------------------------------------------------------- Claude Code (OTel-sourced)


@router.get("/api/telemetry/claude-code/usage")
async def telemetry_claude_code_usage(period: str = "today") -> Dict[str, Any]:
    """Per-(date, model, query_source) rollup of Claude Code's own OTel metrics
    export (issue #68, day breakdown issue #233), persisted at
    ``POST /v1/metrics`` (``src/server_otel_receiver.py`` ->
    ``src/claude_code_otel.py``).

    Deliberately separate from the Code tab's JSONL-sourced totals — this
    source is the only one that sees sub-agent (Task tool) usage, but it is
    not summed into the Code tab's headline numbers to avoid double-counting
    main-agent activity that both sources would otherwise report.
    """
    return _claude_code_otel_summary(period=period)


# ---------------------------------------------------------------- trace detail (row expand)


@router.get("/api/telemetry/trace/{trace_id}")
async def telemetry_trace_detail(trace_id: str) -> Dict[str, Any]:
    """Detail payload for a single trace ID — driven by the SPA's
    row-expand UX. Combines two sources:

    1. Whatever the in-memory OBS ring has on that trace_id (always
       available, instant — model, backend, status, latency, tokens,
       error_detail).
    2. Prompt / completion bodies pulled from Langfuse via the public
       API (only when the stack is up and authed). When Langfuse is
       unreachable we still return the OBS slice + an empty Langfuse
       block, so the panel still renders something useful.

    Validates the trace_id shape; returns 400 on garbage.
    """
    tid = _normalize_trace_id(trace_id)

    obs_match: Dict[str, Any] = {}
    for rec in OBS.recent_requests(limit=200):
        if rec.get("trace_id") == tid:
            obs_match = rec
            break

    langfuse_block: Dict[str, Any] = {
        "available": False,
        "input": None,
        "output": None,
        "fetch_error": "",
    }
    auth = langfuse_basic_auth()
    if auth:
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S * 2) as client:
                r = await client.get(
                    f"{_langfuse_host()}/api/public/traces/{tid}",
                    headers={"Authorization": auth},
                )
            if r.status_code == 200:
                body = r.json() or {}
                # Langfuse v3 trace shape: top-level `input` / `output`
                # plus a list of observations. We surface the trace
                # I/O for the quick-look pane; the deep-link goes to
                # the Langfuse UI for the full timeline.
                langfuse_block.update(
                    available=True,
                    input=_truncate_for_inline(body.get("input")),
                    output=_truncate_for_inline(body.get("output")),
                )
            elif r.status_code == 404:
                langfuse_block["fetch_error"] = (
                    "not found in Langfuse yet (export batches every ~5s)"
                )
            else:
                langfuse_block["fetch_error"] = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            langfuse_block["fetch_error"] = f"{type(exc).__name__}: {exc}"

    return {
        "trace_id": tid,
        "obs": obs_match,
        "langfuse": langfuse_block,
    }


def _truncate_for_inline(value: Any, limit: int = 4000) -> Any:
    """Cap a Langfuse input/output payload at ~4 KB for the inline panel.

    Returns the value unchanged if small; otherwise a string repr
    truncated to ``limit`` chars with an indicator. Leaves dicts/lists
    intact when they fit so the SPA can pretty-print them if it wants.
    """
    if value is None:
        return None
    try:
        as_str = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        as_str = str(value)
    if len(as_str) <= limit:
        return value
    return as_str[:limit] + f"\n…[truncated {len(as_str) - limit} chars]"


# ---------------------------------------------------------------- feedback (scores)


class FeedbackBody(BaseModel):
    thumbs: conint(ge=-1, le=1) = Field(  # type: ignore[valid-type]
        ...,
        description="Score value: -1 (thumbs down), 0 (neutral), +1 (thumbs up)",
    )
    comment: Optional[str] = Field(default=None, max_length=2000)


def _normalize_trace_id(raw: str) -> str:
    """Lowercase + strip; validate the shape (32-hex). Raises 400 otherwise."""
    s = (raw or "").strip().lower()
    if not _TRACE_ID_RE.match(s):
        raise HTTPException(
            status_code=400,
            detail="trace_id must be 32 lowercase hex characters",
        )
    return s


def _push_score_to_langfuse(trace_id: str, value: int, comment: Optional[str]) -> None:
    """Send a score to Langfuse via the SDK. Runs in a background task —
    errors are logged but never surfaced to the client (the score is a
    nice-to-have, not load-bearing for the call itself)."""
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY") or ""
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY") or ""
    if not public_key or not secret_key:
        logger.warning(
            "⚠️ LANGFUSE_PUBLIC_KEY/SECRET_KEY not set — score for trace=%s dropped",
            trace_id,
        )
        return
    try:
        from langfuse import Langfuse  # type: ignore

        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=_langfuse_host(),
        )
        # Langfuse SDK v4 — `create_score` replaces the v2 `score` shortcut.
        client.create_score(
            name="thumbs",
            value=float(value),
            trace_id=trace_id,
            comment=comment,
            data_type="NUMERIC",
        )
        try:
            client.flush()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "⚠️ Langfuse score upload failed for trace=%s: %s", trace_id, exc
        )


@router.post("/api/trace/{trace_id}/feedback")
async def trace_feedback(
    trace_id: str,
    body: FeedbackBody,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """Attach a thumbs ±1 score (plus optional comment) to a Langfuse trace.

    Fire-and-forget — returns 202 in <50 ms while the Langfuse upload
    runs in the background.
    """
    tid = _normalize_trace_id(trace_id)
    background_tasks.add_task(
        _push_score_to_langfuse, tid, int(body.thumbs), body.comment
    )
    return {"accepted": True, "trace_id": tid, "thumbs": int(body.thumbs)}
