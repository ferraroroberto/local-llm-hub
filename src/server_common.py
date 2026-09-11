"""Helpers shared by the hub's route modules.

``server.py`` (chat routes + app), ``server_audio_asr.py`` / ``server_audio_tts.py``
(the ``/v1/audio/*`` proxy) and ``server_images.py`` (the ``/v1/images/*``
handlers) all need the same small set of model-resolution and OpenTelemetry
helpers. They live here, in a leaf module with no dependency on the FastAPI
``app``, so the route modules can import them without a circular import back
into ``server.py``.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager, nullcontext
from typing import Any, Iterator, Optional

from fastapi import HTTPException, Request

from .model_registry import Model, enabled_models, resolve as resolve_model

logger = logging.getLogger(__name__)


def resolve_model_or_400(model_name: str) -> Model:
    """Resolve a request's ``model`` against the registry, or 400 with the
    list of names enabled on this host."""
    m = resolve_model(model_name)
    if m is None:
        known = [m.display_name for m in enabled_models()]
        raise HTTPException(
            status_code=400,
            detail=f"unknown model {model_name!r}. available on this host: {known}",
        )
    return m


def ensure_backend_ready_or_503(model: Model) -> None:
    """Bring a ``startup: on_demand`` local backend up before dispatch (#422).

    No-op for eager rows and remote-owned models (``src.on_demand`` decides).
    A cold on-demand backend is spawned here and the call blocks until it
    answers — so the first request pays the load, exactly like the lazy
    whisper proxy. A spawn/readiness failure surfaces as a distinct 503
    rather than the generic connect-error 502, so "model failed to load" is
    tellable apart from "model crashed mid-serve" in client logs.
    """
    from . import on_demand

    try:
        on_demand.ensure_ready(model)
    except on_demand.OnDemandNotReady as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def client_id_from(request: Request) -> str:
    """Read ``X-Client-Id`` for telemetry attribution; empty string when absent."""
    return (request.headers.get("x-client-id") or "").strip()


def current_otel_span():
    """Return the active OTel span (or None when the SDK is unavailable)."""
    try:
        from opentelemetry import trace as _trace

        return _trace.get_current_span()
    except Exception:  # noqa: BLE001
        return None


def get_tracer(name: str):
    """Return the named OTel tracer, or ``None`` when the SDK is unavailable.

    Shared by ``claude_cli.py`` / ``gemini_cli.py`` (each used to carry its
    own near-identical ``_tracer()`` behind a bare ``try/except``) so a CLI
    wrapper that starts its own span for the subprocess call doesn't
    re-import ``opentelemetry.trace`` inline.
    """
    try:
        from opentelemetry import trace as _trace

        return _trace.get_tracer(name)
    except Exception:  # noqa: BLE001
        return None


@contextmanager
def start_span(tracer_name: str, span_name: str) -> Iterator[Any]:
    """Start ``span_name`` on the named tracer, or a no-op context when the
    OTel SDK is unavailable.

    Shared by ``claude_cli.py`` / ``gemini_cli.py`` (each used to carry its
    own copy of the ``get_tracer(...)`` + ``start_as_current_span(...) if
    tracer is not None else contextlib.nullcontext(None)`` boilerplate)
    so a CLI wrapper that wants to time its own subprocess call just does
    ``with start_span(...) as span:``.
    """
    tracer = get_tracer(tracer_name)
    cm = (
        tracer.start_as_current_span(span_name)
        if tracer is not None
        else nullcontext(None)
    )
    with cm as span:
        yield span


@contextmanager
def safe_span(label: str = "span") -> Iterator[None]:
    """Swallow-and-log wrapper for best-effort OTel span mutations.

    Telemetry must never fail a request, so every span attribute / event
    write is wrapped. Centralising the broad-except here means one
    structured warning — instead of a handful of silent
    ``except Exception: pass`` blocks scattered across the route handlers
    where a real error (a typo'd attribute name, a None span context) would
    be swallowed without a trace. Callers still guard ``span is not None``
    before entering, so the no-op-span case never reaches this except.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
        logger.warning("⚠️ span telemetry (%s) failed: %s", label, exc)


def record_first_token(span: Any, start_ns: int) -> int:
    """Mark a stream's first content token on ``span``: a ``first_token``
    event plus the time-to-first-token attribute, both measured from
    ``start_ns``. Returns the first-token timestamp (monotonic ns) for the
    caller to hand back to :func:`record_last_token`.
    """
    first_token_ns = time.monotonic_ns()
    if span is not None and hasattr(span, "add_event"):
        with safe_span("first_token"):
            ttft_ms = (first_token_ns - start_ns) / 1e6
            span.add_event("first_token", attributes={"latency_ms": ttft_ms})
            span.set_attribute("gen_ai.response.time_to_first_token_ms", ttft_ms)
    return first_token_ns


def record_last_token(
    span: Any, start_ns: int, first_token_ns: Optional[int], output_tokens: int
) -> None:
    """Mark a cleanly-finished stream on ``span``: a ``last_token`` event
    and, when a first token was seen and usage was reported, the decode
    rate (``output_tokens`` over the first-to-last-token window).
    """
    last_ns = time.monotonic_ns()
    if span is not None and hasattr(span, "add_event"):
        with safe_span("last_token"):
            span.add_event(
                "last_token", attributes={"latency_ms": (last_ns - start_ns) / 1e6}
            )
            if first_token_ns is not None and output_tokens > 0:
                seconds = max(1e-6, (last_ns - first_token_ns) / 1e9)
                span.set_attribute(
                    "gen_ai.response.tokens_per_second", output_tokens / seconds
                )


def stash_trace_id_on_ctx(ctx, span) -> None:
    """Copy the span's trace ID onto the live-ring obs context so the
    /admin Hub tab can deep-link into Langfuse."""
    if ctx is None or span is None:
        return
    with safe_span("stash_trace_id"):
        sctx = span.get_span_context()
        if sctx and sctx.trace_id:
            ctx.trace_id = format(sctx.trace_id, "032x")
