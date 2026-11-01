"""Unit tests for the X-Trace-Id header synthesis logic.

We exercise the static method ``_maybe_synthesize_traceparent`` directly
rather than booting a full ASGI app — that's where the only
non-trivial branching lives. The send-wrapper side (X-Trace-Id response
header) is covered by the integration tests against /v1/messages.
"""

from __future__ import annotations

from src.trace_id_middleware import TraceIdHeaderMiddleware


def _scope(path: str = "/v1/messages", headers=None) -> dict:
    return {
        "type": "http",
        "path": path,
        "headers": list(headers or []),
    }


def test_no_x_trace_id_no_change():
    scope = _scope(headers=[(b"content-type", b"application/json")])
    out = TraceIdHeaderMiddleware._maybe_synthesize_traceparent(scope)
    names = [n for n, _ in out["headers"]]
    assert b"traceparent" not in names


def test_traceparent_present_takes_precedence():
    # When the client already sent a W3C traceparent, we leave it alone
    # even if X-Trace-Id is also present.
    tp = b"00-aabbccddeeff0011aabbccddeeff0011-0011223344556677-01"
    scope = _scope(headers=[
        (b"traceparent", tp),
        (b"x-trace-id", b"deadbeefdeadbeefdeadbeefdeadbeef"),
    ])
    out = TraceIdHeaderMiddleware._maybe_synthesize_traceparent(scope)
    # Only one traceparent header in the output, and it's the original.
    tps = [v for n, v in out["headers"] if n == b"traceparent"]
    assert tps == [tp]


def test_x_trace_id_synthesises_traceparent():
    scope = _scope(headers=[(b"x-trace-id", b"9e108e0e-3a5b-4d8c-9f10-1234567890ab")])
    out = TraceIdHeaderMiddleware._maybe_synthesize_traceparent(scope)
    tps = [v for n, v in out["headers"] if n == b"traceparent"]
    assert len(tps) == 1
    parts = tps[0].decode("ascii").split("-")
    # version-traceid(32)-spanid(16)-flags
    assert parts[0] == "00"
    assert len(parts[1]) == 32
    assert len(parts[2]) == 16
    assert parts[3] == "01"


def test_x_trace_id_is_deterministic():
    # Two requests with the same client trace ID must produce the same
    # OTel trace ID (the span ID is random per call).
    scope_a = _scope(headers=[(b"x-trace-id", b"session-42")])
    scope_b = _scope(headers=[(b"x-trace-id", b"session-42")])
    out_a = TraceIdHeaderMiddleware._maybe_synthesize_traceparent(scope_a)
    out_b = TraceIdHeaderMiddleware._maybe_synthesize_traceparent(scope_b)
    tp_a = next(v for n, v in out_a["headers"] if n == b"traceparent")
    tp_b = next(v for n, v in out_b["headers"] if n == b"traceparent")
    trace_a = tp_a.decode("ascii").split("-")[1]
    trace_b = tp_b.decode("ascii").split("-")[1]
    assert trace_a == trace_b


def test_static_path_bypass():
    scope = _scope(
        path="/admin/static/main.js",
        headers=[(b"x-trace-id", b"9e108e0e-3a5b-4d8c-9f10-1234567890ab")],
    )
    out = TraceIdHeaderMiddleware._maybe_synthesize_traceparent(scope)
    names = [n for n, _ in out["headers"]]
    assert b"traceparent" not in names


def test_empty_x_trace_id_no_synth():
    scope = _scope(headers=[(b"x-trace-id", b"")])
    out = TraceIdHeaderMiddleware._maybe_synthesize_traceparent(scope)
    names = [n for n, _ in out["headers"]]
    assert b"traceparent" not in names


def _span_trace_ids_for(headers: dict, monkeypatch) -> tuple[str, list[str]]:
    """Drive one request through a FastAPI app wired like ``src.server`` and
    return ``(response X-Trace-Id, trace ids of the exported spans)``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from src.trace_id_middleware import install_outermost

    # tests/conftest.py disables the SDK; a live span needs it back on.
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict:
        return {}

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    install_outermost(app)
    try:
        resp = TestClient(app).get("/ping", headers=headers)
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    spans = [format(s.context.trace_id, "032x") for s in exporter.get_finished_spans()]
    return resp.headers.get("x-trace-id", ""), spans


def test_x_trace_id_alone_chooses_the_server_trace(monkeypatch):
    # The OTel FastAPI instrumentation wraps the whole stack, so the mapping
    # only counts when it runs outside that layer.
    from src.observability import derive_trace_id_from_uuid

    want = format(derive_trace_id_from_uuid("session-42"), "032x")
    echoed, spans = _span_trace_ids_for({"X-Trace-Id": "session-42"}, monkeypatch)
    assert spans, "no span was exported"
    assert set(spans) == {want}
    assert echoed == want
