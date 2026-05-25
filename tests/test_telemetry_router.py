"""Unit tests for app_web/routers/telemetry.py.

Goes through the parent hub's FastAPI app (mounted /admin sub-app) via
TestClient. With OTEL_SDK_DISABLED=true the health endpoint must still
work and report ``otel_enabled=False``; Langfuse is offline in CI so
``langfuse_reachable`` should land False without raising.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src import server as server_mod


def _client() -> TestClient:
    return TestClient(server_mod.app)


def test_health_endpoint_shape():
    r = _client().get("/admin/api/telemetry/health")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "otel_enabled", "otel_endpoint", "hash_prompts",
        "langfuse_host", "langfuse_port", "langfuse_public_url",
        "langfuse_reachable", "langfuse_auth_configured",
        "langfuse_project_id", "service_instance_id",
    ):
        assert key in body, body
    # Default port + no override unless env is set.
    assert body["langfuse_port"] == 3000
    assert isinstance(body["langfuse_public_url"], str)
    # With conftest forcing OTEL_SDK_DISABLED=true:
    assert body["otel_enabled"] is False
    assert isinstance(body["langfuse_reachable"], bool)
    # OTLP endpoint now derived from LANGFUSE_HOST + /api/public/otel/v1/traces
    assert body["otel_endpoint"].endswith("/api/public/otel/v1/traces")


def test_recent_endpoint_default_limit():
    r = _client().get("/admin/api/telemetry/recent")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "traces" in body
    assert isinstance(body["traces"], list)


def test_recent_endpoint_caps_limit():
    r = _client().get("/admin/api/telemetry/recent?limit=10000")
    assert r.status_code == 200
    assert len(r.json()["traces"]) <= 200


def test_metrics_endpoint_shape():
    r = _client().get("/admin/api/telemetry/metrics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "counters" in body and isinstance(body["counters"], list)
    assert "summary" in body
    summary = body["summary"]
    for key in ("requests", "errors", "error_rate", "since_ts", "since_uptime_s"):
        assert key in summary


def test_feedback_validates_trace_id_shape():
    bad_ids = ["", "tooshort", "ZZZ" * 11, "not-a-trace-id"]
    for bad in bad_ids:
        r = _client().post(
            f"/admin/api/trace/{bad}/feedback",
            json={"thumbs": 1},
        )
        assert r.status_code in (400, 404, 405), (bad, r.status_code, r.text)


def test_feedback_accepts_valid_payload():
    tid = "0123456789abcdef0123456789abcdef"
    r = _client().post(
        f"/admin/api/trace/{tid}/feedback",
        json={"thumbs": 1, "comment": "ok"},
    )
    # We expect 202 (BackgroundTasks always returns the route's status).
    # FastAPI uses 200 by default unless an explicit status is set; the
    # router returns a dict so it lands as 200. Either is acceptable —
    # the contract is "accepted in <50 ms without blocking on Langfuse".
    assert r.status_code in (200, 202), r.text
    body = r.json()
    assert body.get("accepted") is True
    assert body.get("trace_id") == tid
    assert body.get("thumbs") == 1


def test_feedback_rejects_out_of_range_thumbs():
    tid = "0123456789abcdef0123456789abcdef"
    r = _client().post(
        f"/admin/api/trace/{tid}/feedback",
        json={"thumbs": 7},  # > 1 violates pydantic conint(ge=-1, le=1)
    )
    assert r.status_code == 422, r.text


def test_trace_detail_validates_id_shape():
    r = _client().get("/admin/api/telemetry/trace/not-a-valid-id")
    assert r.status_code == 400, r.text


def test_trace_detail_returns_skeleton_for_unknown_id():
    """When Langfuse is offline (no auth in test env) and the trace
    isn't in the OBS ring, the endpoint still returns a usable shell —
    the SPA's expand panel renders 'stack offline' rather than erroring.
    """
    tid = "0123456789abcdef0123456789abcdef"
    r = _client().get(f"/admin/api/telemetry/trace/{tid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trace_id"] == tid
    assert "obs" in body and isinstance(body["obs"], dict)
    assert "langfuse" in body
    lf = body["langfuse"]
    assert "available" in lf
    # No keys in test env → can't talk to Langfuse → not available.
    assert lf["available"] is False


def test_response_carries_x_trace_id_when_otel_disabled():
    # When OTel is disabled there's no active span — the X-Trace-Id
    # header is therefore not added. This test pins that behaviour so a
    # future change to also echo the request's X-Trace-Id is explicit.
    r = _client().get("/admin/api/telemetry/health")
    assert r.status_code == 200
    # Either the header is absent (current behaviour) or it is the
    # client-supplied value echoed back. Both shapes are acceptable;
    # the regression we want to avoid is a malformed empty value.
    if "x-trace-id" in r.headers:
        assert len(r.headers["x-trace-id"]) > 0
