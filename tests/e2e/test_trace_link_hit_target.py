"""A Hub request's trace is a real 44px target and keeps every OTel function (#647, TOUCH-01; #738).

The link once sat inline in the token cell at 26x16 px. Since #738 the whole
Activity row is the target: it opens the request's trace sheet, which carries
the OTel pane's trace functions: the prompt and completion preview (its
fallback message while Langfuse is offline), a thumbs up and a thumbs down
score, and the open-in-Langfuse link. Stubs the live-request stream, the trace
detail and the feedback endpoint, then measures and drives them on a phone and
on desktop.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e._surface import surface

pytestmark = pytest.mark.usefixtures("admin_url")

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}

_SERVICES = {
    "launchable": True,
    "docker": {"running": True, "server_version": "27.0"},
    "langfuse": {"reachable": True},
    "agentsview": {"host": None},
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
    frames = "".join(f"data: {json.dumps(_request(i))}\n\n" for i in range(3))
    page.route(
        "**/admin/api/hub/requests/stream*",
        lambda route: route.fulfill(
            status=200, content_type="text/event-stream", body=":open\nretry: 60000\n\n" + frames),
    )
    # Langfuse offline: the detail endpoint answers without the bodies.
    page.route(
        "**/admin/api/telemetry/trace/*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "trace_id": "trace-0001-abcdef", "langfuse": {"available": False}})),
    )
    scores = []

    def feedback(route):
        scores.append(route.request.post_data_json["thumbs"])
        route.fulfill(status=200, content_type="application/json", body="{}")

    page.route("**/admin/api/trace/*/feedback", feedback)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector('#liveRequestsList [data-act="trace"]', state="visible", timeout=10000)

    rects = page.evaluate(
        """() => [...document.querySelectorAll('#liveRequestsList [data-act="trace"]')].map(a => {
            const r = a.getBoundingClientRect();
            return { w: r.width, h: r.height, top: r.top, bottom: r.bottom };
        })"""
    )
    assert len(rects) == 3, rects
    for r in rects:
        assert r["w"] >= 44 and r["h"] >= 44, f"trace target is {r['w']}x{r['h']}: {rects}"
    ordered = sorted(rects, key=lambda r: r["top"])
    for a, b in zip(ordered, ordered[1:]):
        assert a["bottom"] <= b["top"] + 0.5, f"trace targets overlap: {ordered}"
    surface(page, "hub.activity-requests")

    # The trace sheet: preview fallback, both scores, the Langfuse link.
    page.locator('#liveRequestsList [data-act="trace"]').first.click()
    sheet = page.locator("#traceSheet[open]")
    sheet.wait_for(timeout=3000)
    page.wait_for_function(
        "(document.querySelector('#traceSheet .trace-detail')?.innerText || '').includes('Langfuse stack offline')",
        timeout=5000,
    )
    sheet.locator('.trace-thumb[data-thumbs="1"]').click()
    sheet.locator('.trace-thumb[data-thumbs="-1"]').click()
    for _ in range(30):
        if len(scores) >= 2:
            break
        page.wait_for_timeout(100)
    assert scores == [1, -1], scores
    link = sheet.locator("a.trace-link")
    assert "trace-0002-abcdef" in link.get_attribute("href")
    box = link.bounding_box()
    assert box["height"] >= 44, box
    surface(page, "hub.trace-sheet-offline")
