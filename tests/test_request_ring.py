"""A routed request lands in the live request ring (#599).

Moved out of the browser suite: neither test ever opened a page. They drive
the parent hub's ASGI app through TestClient, so the request passes the same
``ObservatoryMiddleware`` that fills ``OBS`` in the running hub, and read the
ring back through both surfaces that show it — the Hub tab's
``/api/hub/requests/recent`` and the Telemetry tab's ``/api/telemetry/recent``.
The SSE delivery of those rows into the DOM stays in the e2e suite
(``test_live_requests_stream_rolls_forward``).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from src import server as server_mod


def _post_unknown_model(client: TestClient) -> str:
    """Send a /v1/messages call the router rejects; return its unique model.

    A per-call marker matches *this* row, never an ordering assumption on the
    ring's head or a stale row left by another test in the same process.
    """
    marker = f"ring-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/v1/messages",
        json={"model": marker, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400, r.text
    return marker


def test_rejected_request_lands_in_hub_ring():
    client = TestClient(server_mod.app)
    marker = _post_unknown_model(client)

    body = client.get("/admin/api/hub/requests/recent").json()
    row = next((q for q in body["requests"] if q["model"] == marker), None)
    assert row is not None, f"marker {marker} never landed in the ring: {body}"
    assert row["status"] == 400
    # Monotonic-clock duration: non-negative, but can read 0.0 below the
    # clock's resolution.
    assert row["latency_ms"] >= 0


def test_rejected_request_surfaces_in_telemetry_recent():
    client = TestClient(server_mod.app)
    marker = _post_unknown_model(client)

    traces = client.get("/admin/api/telemetry/recent").json().get("traces") or []
    assert any(t.get("model") == marker for t in traces), (
        f"marker {marker} never showed in /admin/api/telemetry/recent"
    )
