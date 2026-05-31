"""OpenTelemetry bootstrap for the local LLM hub (issue #4).

Initialises tracing + metrics + log-record correlation, exporting via
OTLP/gRPC to a local Langfuse stack (see
``docker/langfuse/docker-compose.yml``).

Design points:

- **Idempotent.** ``init_otel()`` is safe to call multiple times; the
  module-level guard means uvicorn's reload mode does not re-stack
  exporters.
- **Soft-fails.** Any exception during init is logged and swallowed —
  the hub must keep serving traffic even if the OTLP endpoint is down,
  the network is gone, or the SDK is mis-installed.
- **Switch-off.** ``OTEL_SDK_DISABLED=true`` (read directly here, since
  ``opentelemetry-sdk`` only honours it inside its own provider code)
  short-circuits the whole init and returns a NoOpTracer / NoOpMeter.
- **PII switch.** :func:`set_genai_payload` hashes prompt/completion
  bodies when ``OTEL_HASH_PROMPTS=true``; otherwise stores raw text.
  Default = capture raw (this is a personal-localhost hub).
- **W3C ↔ UUID4 bridge.** :func:`derive_trace_id_from_uuid` maps any
  caller-supplied UUID4/hex string deterministically to an OTel 128-bit
  trace ID, so clients that pass ``X-Trace-Id: <uuid4>`` instead of a
  full W3C ``traceparent`` still get correlated traces.

The hub's hot path uses three things from here:

1. ``init_otel()`` — once, near the top of ``src/server.py``.
2. ``genai_meters()`` — returns the pre-created histogram + counters
   the middleware updates per request.
3. ``set_genai_payload(span, prompt, completion)`` — called by the
   handlers after collecting the request + response bodies.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import socket
import threading
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_INIT_LOCK = threading.Lock()
_INITIALISED = False
_RESOURCE_INSTANCE_ID = ""

# Langfuse v3 self-hosted exposes a single OTLP/HTTP receiver under the
# main web port; there is no separate gRPC endpoint and no native :4317
# listener. The traces path is fixed by Langfuse; clients only need the
# host. Auth is Basic against the project's public/secret key pair.
DEFAULT_LANGFUSE_HOST = "http://localhost:3000"
LANGFUSE_OTLP_TRACES_PATH = "/api/public/otel/v1/traces"
LANGFUSE_OTLP_METRICS_PATH = "/api/public/otel/v1/metrics"

SERVICE_VERSION_DEFAULT = "0.3.0"


def is_sdk_disabled() -> bool:
    """Read the standard OTel kill-switch ourselves so callers can branch
    cheaply without importing the SDK."""
    return os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in {"1", "true", "yes"}


def hash_prompts_enabled() -> bool:
    """Whether prompt/completion bodies should be stored as BLAKE2b hashes."""
    return os.environ.get("OTEL_HASH_PROMPTS", "").strip().lower() in {"1", "true", "yes"}


# ----------------------------------------------------------------- metrics
# The hub creates exactly one of each metric instrument; the middleware /
# handlers use module-level singletons.

@dataclass
class _Meters:
    op_duration: object  # Histogram | NoOpHistogram
    token_usage: object  # Counter   | NoOpCounter
    requests_total: object  # Counter
    upstream_errors_total: object  # Counter


_METERS: Optional[_Meters] = None


def genai_meters() -> Optional[_Meters]:
    """Return the GenAI meter instruments, or None when OTel is disabled."""
    return _METERS


# ----------------------------------------------------------------- helpers

def derive_trace_id_from_uuid(value: str) -> Optional[int]:
    """Map a caller-supplied UUID4 / hex string to a 128-bit OTel trace ID.

    Accepts: 32-char hex (already an OTel trace ID), or a UUID in any
    common form (hyphenated or not). Anything else → ``None`` so the
    caller can fall back to generating a fresh trace ID.

    Determinism matters: two requests with the same client-side trace
    ID must land in the same Langfuse trace, so we BLAKE2b the input
    into a stable 16-byte digest.
    """
    raw = (value or "").strip().lower()
    if not raw:
        return None

    # Plain 32-char hex — already a valid OTel trace ID.
    if len(raw) == 32:
        try:
            int(raw, 16)
            return int(raw, 16) or None  # all-zero is invalid in OTel
        except ValueError:
            pass

    # Try UUID4 in either hyphenated or compact form.
    candidate: Optional[str] = None
    try:
        candidate = uuid.UUID(raw).hex
    except (ValueError, AttributeError):
        candidate = None

    if candidate is None:
        # Last resort: BLAKE2b the original string into a 16-byte ID so
        # callers passing arbitrary opaque strings still get correlation.
        digest = hashlib.blake2b(raw.encode("utf-8"), digest_size=16).digest()
        as_int = int.from_bytes(digest, "big")
        return as_int or None

    # Hash the UUID hex too so we don't collide with a real OTel trace
    # ID that happens to share the same 32-char prefix.
    digest = hashlib.blake2b(candidate.encode("utf-8"), digest_size=16).digest()
    as_int = int.from_bytes(digest, "big")
    return as_int or None


_MAX_INLINE_PAYLOAD = 8 * 1024  # 8 KB cap on raw-text span attrs


def _hash_payload(text: str) -> str:
    """BLAKE2b digest of a payload, hex-encoded with a short prefix."""
    if not text:
        return ""
    digest = hashlib.blake2b(text.encode("utf-8", errors="replace"), digest_size=16).hexdigest()
    return f"blake2b:{digest}"


def set_genai_payload(span, prompt: Optional[str], completion: Optional[str]) -> None:
    """Attach the prompt + completion bodies to a span per the PII flag.

    Sets two parallel attribute families:

    1. **OTel GenAI semconv** (``gen_ai.prompt`` / ``gen_ai.completion``)
       — what the spec recommends; vendor-neutral.
    2. **Langfuse-specific** (``langfuse.trace.input`` / ``.output``,
       ``langfuse.observation.input`` / ``.output``, plus
       ``langfuse.observation.type=generation``) — what the Langfuse OTel
       ingester actually reads to populate trace-level + observation
       I/O. Without these, the Telemetry tab's expand panel and the
       Langfuse UI both show "(empty)".

    Stores raw text when ``OTEL_HASH_PROMPTS`` is off, hashes when on.
    Truncates raw text at 8 KB so a giant context window doesn't blow
    up the OTLP export.
    """
    if span is None or not hasattr(span, "set_attribute"):
        return
    try:
        prompt_body = ""
        prompt_truncated = False
        if prompt:
            if hash_prompts_enabled():
                prompt_body = _hash_payload(prompt)
            elif len(prompt) <= _MAX_INLINE_PAYLOAD:
                prompt_body = prompt
            else:
                prompt_body = prompt[: _MAX_INLINE_PAYLOAD]
                prompt_truncated = True

        completion_body = ""
        completion_truncated = False
        if completion:
            if hash_prompts_enabled():
                completion_body = _hash_payload(completion)
            elif len(completion) <= _MAX_INLINE_PAYLOAD:
                completion_body = completion
            else:
                completion_body = completion[: _MAX_INLINE_PAYLOAD]
                completion_truncated = True

        if prompt_body:
            # OTel GenAI semconv
            span.set_attribute("gen_ai.prompt", prompt_body)
            # Langfuse — trace + observation level. The trace-level
            # attrs are what the SPA's inline expand panel fetches via
            # GET /api/public/traces/{id}.input.
            span.set_attribute("langfuse.trace.input", prompt_body)
            span.set_attribute("langfuse.observation.input", prompt_body)
            if prompt_truncated:
                span.set_attribute("gen_ai.prompt.truncated", True)
                span.set_attribute("gen_ai.prompt.original_length", len(prompt or ""))
        if completion_body:
            span.set_attribute("gen_ai.completion", completion_body)
            span.set_attribute("langfuse.trace.output", completion_body)
            span.set_attribute("langfuse.observation.output", completion_body)
            if completion_truncated:
                span.set_attribute("gen_ai.completion.truncated", True)
                span.set_attribute("gen_ai.completion.original_length", len(completion or ""))
        if prompt_body or completion_body:
            # Mark the span as an LLM-style generation so Langfuse shows
            # it in the right pane (with model + usage instead of as a
            # plain HTTP span).
            span.set_attribute("langfuse.observation.type", "generation")
    except Exception:  # noqa: BLE001 — telemetry must never break the request
        logger.debug("set_genai_payload failed", exc_info=True)


def service_instance_id() -> str:
    """Return the resource-level service.instance.id used by Langfuse to
    distinguish multiple hub hosts."""
    return _RESOURCE_INSTANCE_ID


def langfuse_host() -> str:
    """Return the Langfuse base URL (no trailing slash)."""
    return os.environ.get("LANGFUSE_HOST", DEFAULT_LANGFUSE_HOST).rstrip("/")


def langfuse_otlp_traces_endpoint() -> str:
    """Full URL the OTel HTTP exporter posts spans to."""
    return langfuse_host() + LANGFUSE_OTLP_TRACES_PATH


def langfuse_otlp_metrics_endpoint() -> str:
    """Full URL the OTel HTTP exporter posts metrics to."""
    return langfuse_host() + LANGFUSE_OTLP_METRICS_PATH


def langfuse_basic_auth() -> Optional[str]:
    """Build the ``Authorization: Basic ...`` header value from env vars.

    Returns ``None`` when either key is missing — the caller logs a
    warning and skips wiring auth, which means Langfuse will reject
    every span. This is the most common cause of "Trace not found"
    deep-link errors so we surface it loudly.
    """
    pk = (os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    sk = (os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    if not pk or not sk:
        return None
    token = base64.b64encode(f"{pk}:{sk}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def langfuse_otlp_headers() -> Dict[str, str]:
    """Headers the OTel HTTP exporter sends with every export.

    Always includes Content-Type per OTLP/HTTP spec; includes
    Authorization when the project keys are present.
    """
    headers = {"Content-Type": "application/x-protobuf"}
    auth = langfuse_basic_auth()
    if auth:
        headers["Authorization"] = auth
    return headers


# ----------------------------------------------------------------- gen_ai mapping

_BACKEND_TO_GENAI_SYSTEM = {
    "claude": "anthropic",
    "gemini": "google_genai",
    "openai": "llama_cpp",   # our local llama-server backends speak OpenAI shape
    "whisper": "whisper",
}


def backend_to_genai_system(backend: str) -> str:
    """Map an internal backend id to the OTel GenAI semconv ``gen_ai.system`` value."""
    return _BACKEND_TO_GENAI_SYSTEM.get(backend or "", backend or "unknown")


def set_genai_request_attrs(
    span,
    *,
    model: str,
    backend: str,
    operation: str = "chat",
    temperature=None,
    max_tokens=None,
    client_id: str = "",
) -> None:
    """Set the standard GenAI request attributes on the active span.

    Safe to call when ``span`` is a no-op (telemetry disabled).
    """
    if span is None or not hasattr(span, "set_attribute"):
        return
    try:
        span.set_attribute("gen_ai.system", backend_to_genai_system(backend))
        span.set_attribute("gen_ai.operation.name", operation)
        if model:
            span.set_attribute("gen_ai.request.model", model)
        if temperature is not None:
            span.set_attribute("gen_ai.request.temperature", float(temperature))
        if max_tokens is not None:
            span.set_attribute("gen_ai.request.max_tokens", int(max_tokens))
        if client_id:
            span.set_attribute("client.id", client_id)
    except Exception:  # noqa: BLE001
        logger.debug("set_genai_request_attrs failed", exc_info=True)


def set_genai_response_attrs(
    span,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    finish_reason: str = "",
    response_id: str = "",
) -> None:
    """Set the standard GenAI response attributes on the active span."""
    if span is None or not hasattr(span, "set_attribute"):
        return
    try:
        if input_tokens:
            span.set_attribute("gen_ai.usage.input_tokens", int(input_tokens))
        if output_tokens:
            span.set_attribute("gen_ai.usage.output_tokens", int(output_tokens))
        if finish_reason:
            span.set_attribute("gen_ai.response.finish_reasons", [finish_reason])
        if response_id:
            span.set_attribute("gen_ai.response.id", response_id)
    except Exception:  # noqa: BLE001
        logger.debug("set_genai_response_attrs failed", exc_info=True)


def record_genai_metrics(
    *,
    model: str,
    backend: str,
    route: str,
    client_id: str,
    duration_ms: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    error_type: str = "",
) -> None:
    """Update all four GenAI metric instruments for a completed request.

    No-op when ``init_otel`` didn't produce a meter set (SDK disabled,
    import failure, etc.).
    """
    meters = _METERS
    if meters is None:
        return
    system = backend_to_genai_system(backend)
    labels = {
        "gen_ai.request.model": model or "unknown",
        "gen_ai.system": system,
    }
    if error_type:
        labels["error.type"] = error_type
    try:
        meters.op_duration.record(float(duration_ms), labels)
        if input_tokens:
            meters.token_usage.add(
                int(input_tokens),
                {**labels, "gen_ai.token.type": "input"},
            )
        if output_tokens:
            meters.token_usage.add(
                int(output_tokens),
                {**labels, "gen_ai.token.type": "output"},
            )
        meters.requests_total.add(
            1,
            {"route": route, "client": client_id or "unknown"},
        )
        if error_type:
            meters.upstream_errors_total.add(
                1,
                {"gen_ai.system": system, "error.type": error_type},
            )
    except Exception:  # noqa: BLE001 — metrics must never break the request
        logger.debug("record_genai_metrics failed", exc_info=True)


# ----------------------------------------------------------------- init

def init_otel(
    service_name: str = "local-llm-hub",
    service_version: str = SERVICE_VERSION_DEFAULT,
) -> bool:
    """Bring up tracing + metrics + log-correlation. Idempotent.

    Returns ``True`` if instrumentation is live, ``False`` if disabled
    or if init failed (in which case the hub still works — the OTel
    APIs return no-op providers and span/metric calls become cheap
    no-ops).
    """
    global _INITIALISED, _METERS, _RESOURCE_INSTANCE_ID

    with _INIT_LOCK:
        if _INITIALISED:
            return _METERS is not None

        if is_sdk_disabled():
            logger.info("ℹ️ OTel SDK disabled via OTEL_SDK_DISABLED — telemetry off")
            _INITIALISED = True
            return False

        try:
            from opentelemetry import metrics, trace
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as exc:
            logger.warning(
                "⚠️ OTel SDK not importable (%s) — telemetry off. Install with "
                "`pip install -r requirements.txt`.",
                exc,
            )
            _INITIALISED = True
            return False

        try:
            hostname = socket.gethostname() or "unknown-host"
            instance_id = f"{hostname}-{os.getpid()}"
            _RESOURCE_INSTANCE_ID = instance_id

            traces_endpoint = langfuse_otlp_traces_endpoint()
            metrics_endpoint = langfuse_otlp_metrics_endpoint()
            headers = langfuse_otlp_headers()
            if "Authorization" not in headers:
                logger.warning(
                    "⚠️ LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set — "
                    "OTel spans will be sent unauthenticated and Langfuse "
                    "will reject them. Create a project in the Langfuse UI "
                    "and paste the keys into .env."
                )

            resource = Resource.create(
                {
                    "service.name": os.environ.get("OTEL_SERVICE_NAME", service_name),
                    "service.version": service_version,
                    "service.instance.id": instance_id,
                    "host.name": hostname,
                    "process.pid": os.getpid(),
                }
            )

            # ----- tracing — Langfuse v3 expects OTLP/HTTP with Basic auth
            # against its own port (no separate :4317 collector).
            span_exporter = OTLPSpanExporter(
                endpoint=traces_endpoint,
                headers=headers,
            )
            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            trace.set_tracer_provider(tracer_provider)

            # ----- metrics — same OTLP/HTTP transport
            metric_exporter = OTLPMetricExporter(
                endpoint=metrics_endpoint,
                headers=headers,
            )
            reader = PeriodicExportingMetricReader(
                metric_exporter, export_interval_millis=15_000
            )
            meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(meter_provider)

            meter = metrics.get_meter("local-llm-hub", service_version)
            _METERS = _Meters(
                op_duration=meter.create_histogram(
                    name="gen_ai.client.operation.duration",
                    unit="ms",
                    description="End-to-end latency of a GenAI request, in milliseconds",
                ),
                token_usage=meter.create_counter(
                    name="gen_ai.client.token.usage",
                    unit="{token}",
                    description="Tokens consumed by GenAI requests (input/output)",
                ),
                requests_total=meter.create_counter(
                    name="hub.requests.total",
                    unit="{request}",
                    description="Total requests served by the hub, by route + client",
                ),
                upstream_errors_total=meter.create_counter(
                    name="hub.upstream.errors.total",
                    unit="{error}",
                    description="Upstream errors by gen_ai.system + error.type",
                ),
            )

            # ----- log correlation
            try:
                from opentelemetry.instrumentation.logging import LoggingInstrumentor

                LoggingInstrumentor().instrument(set_logging_format=False)
            except Exception:  # noqa: BLE001 — non-fatal
                logger.debug("LoggingInstrumentor failed to install", exc_info=True)

            # ----- httpx outgoing-request spans (used by openai_upstream)
            try:
                from opentelemetry.instrumentation.httpx import (
                    HTTPXClientInstrumentor,
                )

                HTTPXClientInstrumentor().instrument()
            except Exception:  # noqa: BLE001
                logger.debug("HTTPXClientInstrumentor failed to install", exc_info=True)

            # Silence the OTLP exporter's connect-refused retry/error log
            # noise. When Langfuse is offline the admin SPA's Telemetry
            # tab already shows "Stack offline" — duplicating that as
            # ERROR lines in the Hub log pane just spams the UI. Real
            # protocol-level failures still surface at WARNING.
            for noisy in (
                "opentelemetry.exporter.otlp.proto.http.trace_exporter",
                "opentelemetry.exporter.otlp.proto.http.metric_exporter",
                "opentelemetry.sdk.metrics._internal.export",
                "opentelemetry.sdk.trace.export",
            ):
                logging.getLogger(noisy).setLevel(logging.CRITICAL)

            logger.info(
                "ℹ️ OTel initialised — service=%s instance=%s endpoint=%s auth=%s",
                service_name, instance_id, traces_endpoint,
                "yes" if "Authorization" in headers else "MISSING",
            )
            _INITIALISED = True
            return True
        except Exception as exc:  # noqa: BLE001 — never break startup over telemetry
            logger.warning("⚠️ OTel init failed: %s — telemetry off", exc, exc_info=True)
            _INITIALISED = True
            _METERS = None
            return False


def instrument_fastapi_app(app) -> None:
    """Wrap a FastAPI app with the OTel ASGI middleware (idempotent)."""
    if is_sdk_disabled():
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception as exc:  # noqa: BLE001
        logger.debug("FastAPIInstrumentor.instrument_app failed: %s", exc, exc_info=True)


# ----------------------------------------------------------------- testing
def _reset_for_tests() -> None:
    """Reset the module-level init guard. Tests-only — do not use in prod."""
    global _INITIALISED, _METERS, _RESOURCE_INSTANCE_ID
    with _INIT_LOCK:
        _INITIALISED = False
        _METERS = None
        _RESOURCE_INSTANCE_ID = ""
