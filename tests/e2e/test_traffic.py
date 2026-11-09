"""Hub Activity: live requests, their rows, their traces and the sign-in sheet.

Three phases, each in its own browser context: the live stream delivering real
requests (rows lead with the model, the clock trails, no duplicate on a
reconnect), a stubbed stream whose rows are 44px trace targets that open the
trace sheet with every OTel function, and the typed 401 contract with the
sheet it raises.

Runs after ``test_smoke.py``, whose empty-state checks need a hub that has
served no model traffic.
"""

from __future__ import annotations

import json
import re

import httpx

from tests.e2e._flow import DESKTOP, PHONE, fresh_page
from tests.e2e._surface import surface

CLOCK = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?")

# Counts the Hub tab's live-request EventSource connections and the frames
# they deliver, so the test waits on "stream open" and "seed replayed" instead
# of fixed sleeps. The listener is registered at construction, ahead of the
# app's own ``onmessage``, so a count read from a later task already includes
# that frame's render.
_SSE_TAP = """
(() => {
  const Native = window.EventSource;
  window.__sse = { opened: 0, messages: 0 };
  window.EventSource = class extends Native {
    constructor(url, ...rest) {
      super(url, ...rest);
      if (!String(url).includes('/requests/stream')) return;
      this.addEventListener('open', () => { window.__sse.opened += 1; });
      this.addEventListener('message', () => { window.__sse.messages += 1; });
    }
  };
})();
"""

_SERVICES = {
    "launchable": True,
    "docker": {"running": True, "server_version": "27.0"},
    "langfuse": {"reachable": True},
    "agentsview": {"host": None},
}


def _cells_of(page, list_selector: str, marker: str):
    """The first and last cell of the row in ``list_selector`` that names ``marker``."""
    page.wait_for_function(
        "([sel, m]) => (document.querySelector(sel)?.textContent || '').includes(m)",
        arg=[list_selector, marker],
        timeout=10000,
    )
    return page.evaluate(
        """([sel, m]) => {
            const li = [...document.querySelectorAll(sel + ' > li')]
                .find(l => (l.textContent || '').includes(m));
            const cells = [...li.children].map(c => (c.textContent || '').trim());
            return { first: cells[0], last: cells[cells.length - 1], all: cells.join(' ') };
        }""",
        [list_selector, marker],
    )


def _marker_counts(page, markers):
    return page.evaluate(
        "(markers) => {"
        "  const ul = document.getElementById('liveRequestsList');"
        "  const items = Array.from(ul ? ul.children : []);"
        "  const out = {};"
        "  for (const m of markers) {"
        "    out[m] = items.filter(li => (li.textContent || '').includes(m)).length;"
        "  }"
        "  return out;"
        "}",
        markers,
    )


def _live_stream_rolls_forward(page, admin_url):
    """Two fresh requests reach the list as live frames, each row leads with its
    model and ends on the clock, and a tab-out/tab-in replays the seed without
    duplicating a row."""
    base = admin_url.rsplit("/admin/", 1)[0]
    page.add_init_script(_SSE_TAP)
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub", state="visible", timeout=5000)
    page.wait_for_selector("#liveRequestsList", state="attached", timeout=3000)
    page.wait_for_function("window.__sse.opened >= 1", timeout=5000)

    # Distinguishable model names, so each marker is a unique row.
    marker_a, marker_b = "e2e-rolls-forward-A", "e2e-rolls-forward-B"
    for marker in (marker_a, marker_b):
        r = httpx.post(
            f"{base}/v1/messages",
            json={"model": marker, "messages": [{"role": "user", "content": "hi"}]},
            timeout=5.0,
        )
        assert r.status_code == 400, r.text

    page.wait_for_function(
        "(args) => {"
        "  const ul = document.getElementById('liveRequestsList');"
        "  if (!ul) return false;"
        "  const text = ul.textContent || '';"
        "  return text.indexOf(args.a) !== -1 && text.indexOf(args.b) !== -1;"
        "}",
        arg={"a": marker_a, "b": marker_b},
        timeout=10000,
    )
    counts = _marker_counts(page, [marker_a, marker_b])
    assert counts[marker_a] == 1, f"marker_a duplicated on first delivery: {counts}"
    assert counts[marker_b] == 1, f"marker_b duplicated on first delivery: {counts}"

    # The model leads and the clock is the last, muted cell, in the live list and
    # in the recent-errors list alike.
    for selector in ("#liveRequestsList", "#recentErrorsList"):
        cells = _cells_of(page, selector, marker_a)
        assert marker_a in cells["first"], f"{selector} row opens with {cells['first']!r}"
        assert CLOCK.match(cells["last"]), f"{selector} row ends with {cells['last']!r}"

    # main.js stops the stream on tab-out and starts it again on tab-in; the
    # server then replays its 20-record seed, which used to duplicate every row.
    frames_before = page.evaluate("window.__sse.messages")
    page.click("#tabModels")
    page.wait_for_selector("#paneModels", state="visible", timeout=2000)
    page.click("#tabHub")
    page.wait_for_selector("#paneHub", state="visible", timeout=2000)
    page.wait_for_function(
        "(n) => window.__sse.opened >= 2 && window.__sse.messages >= n + 2",
        arg=frames_before,
        timeout=10000,
    )
    counts_after = _marker_counts(page, [marker_a, marker_b])
    assert counts_after[marker_a] == 1, f"marker_a duplicated after SSE reconnect: {counts_after}"
    assert counts_after[marker_b] == 1, f"marker_b duplicated after SSE reconnect: {counts_after}"


def _request(i: int) -> dict:
    return {"ts": 1_791_120_000 + i, "path": "/v1/messages", "model": "claude-haiku-4-5",
            "backend": "claude", "status": 200, "latency_ms": 900 + i,
            "in_tok": 10, "out_tok": 5, "trace_id": f"trace-{i:04d}-abcdef"}


def _trace_links_are_44px_targets(page, admin_url, mark):
    """The whole Activity row is the target; it opens the trace sheet with the
    preview fallback (Langfuse offline), both scores and the Langfuse link."""
    page.route("**/admin/api/services/status*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(_SERVICES)))
    frames = "".join(f"data: {json.dumps(_request(i))}\n\n" for i in range(3))
    page.route("**/admin/api/hub/requests/stream*", lambda route: route.fulfill(
        status=200, content_type="text/event-stream", body=":open\nretry: 60000\n\n" + frames))
    page.route("**/admin/api/telemetry/trace/*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({
            "trace_id": "trace-0001-abcdef", "langfuse": {"available": False}})))
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
    mark("hub.activity-requests")

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
    assert link.bounding_box()["height"] >= 44
    mark("hub.trace-sheet-offline")


def _auth_error_is_a_typed_contract(page, admin_url, mark):
    """api() signals a 401 with ``AuthRequiredError``; callers test it with
    ``isAuthError`` and never match the message text. The sheet a 401 raises:
    api() calls showLogin() before it throws."""
    page.goto(admin_url, wait_until="domcontentloaded")
    got = page.evaluate(
        """() => import('/admin/static/api.js').then(m => [
            m.isAuthError(new m.AuthRequiredError()),
            m.isAuthError(new Error('auth required')),
        ])"""
    )
    assert got == [True, False]

    page.evaluate("() => import('/admin/static/api.js').then(m => m.showLogin())")
    sheet = page.locator("dialog.sign-in")
    sheet.wait_for(state="visible", timeout=3000)
    assert sheet.locator("input[type=password]").is_visible()
    mark("login.overlay")


def test_hub_activity(browser, admin_url):
    with fresh_page(browser, console_errors=True) as page:
        _live_stream_rolls_forward(page, admin_url)
    for viewport in (PHONE, DESKTOP):
        with fresh_page(browser, viewport=viewport) as page:
            _trace_links_are_44px_targets(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser, console_errors=True) as page:
        _auth_error_is_a_typed_contract(page, admin_url, lambda name: surface(page, name))
