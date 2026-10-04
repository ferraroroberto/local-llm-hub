"""The Hub's per-request ``trace`` link is a real 44px target (#647, TOUCH-01).

The link sat inline in the token cell at 26x16 px, so on any Hub with Langfuse
up and a few requests listed most of the screen's controls were under the
44px floor. Stubs the live-request stream and the services status so the links
render, then measures them on a phone and on desktop.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.usefixtures("admin_url")

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}

_SERVICES = {
    "launchable": True,
    "docker": {"running": True, "server_version": "27.0"},
    "langfuse": {"reachable": True},
    "agentsview": {"host": None},
    "peers": [],
}


def _request(i: int) -> dict:
    return {"ts": 1_791_120_000 + i, "path": "/v1/messages", "model": "claude-haiku-4-5",
            "backend": "claude", "status": 200, "latency_ms": 900 + i,
            "in_tok": 10, "out_tok": 5, "trace_id": f"trace-{i:04d}-abcdef"}


@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_trace_links_are_44px_targets_that_never_overlap(page, admin_url, viewport):
    page.set_viewport_size(viewport)
    page.route(
        "**/admin/api/services/status*",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(_SERVICES)),
    )
    # The rows render a link only once the services status has said Langfuse is
    # up, so the first connection carries nothing and EventSource's reconnect
    # (retry: 200) brings the requests after the status has landed.
    frames = "".join(f"data: {json.dumps(_request(i))}\n\n" for i in range(3))
    calls = []

    def stream(route):
        calls.append(1)
        body = ":open\nretry: 200\n\n" + (frames if len(calls) > 1 else "")
        route.fulfill(status=200, content_type="text/event-stream", body=body)

    page.route("**/admin/api/hub/requests/stream*", stream)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_function(
        "document.getElementById('dockerStatusText').textContent === 'up'", timeout=10000
    )
    # The list lives in a collapsed card; the review walk opens such disclosures.
    page.evaluate("document.getElementById('liveRequestsList').closest('details').open = true")
    page.wait_for_selector("#liveRequestsList li a", state="visible", timeout=10000)

    rects = page.evaluate(
        """() => [...document.querySelectorAll('#liveRequestsList li a')].map(a => {
            const r = a.getBoundingClientRect();
            return { w: r.width, h: r.height, top: r.top, bottom: r.bottom };
        })"""
    )
    assert len(rects) == 3, rects
    for r in rects:
        assert r["w"] >= 44 and r["h"] >= 44, f"trace link is {r['w']}x{r['h']}: {rects}"
    ordered = sorted(rects, key=lambda r: r["top"])
    for a, b in zip(ordered, ordered[1:]):
        assert a["bottom"] <= b["top"] + 0.5, f"trace links overlap: {ordered}"
