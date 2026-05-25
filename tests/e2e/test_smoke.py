"""End-to-end smoke tests on the /admin SPA.

What the gate proves:

  * Each tab pane (Hub / Models / Playground) loads without throwing a
    JavaScript console error.
  * The hub-control round-trip works: a /v1/messages request to a known-
    bad model lands as a row in the live-request ring (SSE arrives).
  * Static assets carry the ?v=<hash> stamp (cache-busting wired up).
  * GET /admin/api/version returns a non-empty git_sha + asset_hash.
  * Phone-viewport (390 x 844) screenshot of each tab is captured for
    visual review — files land in ``tests/e2e/snapshots/`` (gitignored).

Runs under Chromium only. WebKit was dropped from the matrix in issue
#24 — the SPA has no Safari-specific code and WebKit on windows-latest
CI was chronically flaky.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("admin_url")

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
PHONE_VIEWPORT = {"width": 390, "height": 844}


@pytest.fixture(autouse=True)
def _no_console_errors(page):
    """Fail the test if the page logs an actual ``console.error`` line.

    Failures from missing icon-180.png are noise we don't care about
    here — the icon family swap is tracked separately (issue #6). Any
    *other* error fails fast.
    """
    errs = []

    def _on_console(msg):
        if msg.type != "error":
            return
        text = msg.text
        # Tolerated noise — placeholder PNG icons occasionally 404 while
        # the manifest is still settling on first paint.
        if "icon-180" in text or "icon-512" in text:
            return
        errs.append(text)

    page.on("console", _on_console)
    yield
    if errs:
        raise AssertionError("console errors: " + " | ".join(errs))


def test_admin_loads(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#tabHub", state="visible", timeout=5000)
    # Hub tab is the default — wait for state, don't race.
    page.wait_for_selector("#paneHub", state="visible", timeout=3000)
    page.wait_for_selector("#paneModels", state="hidden", timeout=3000)
    page.wait_for_selector("#panePlayground", state="hidden", timeout=3000)


def test_models_tab(page, admin_url):
    # wait_until="load" — not "domcontentloaded" — so the module-script
    # boot() has finished and wireTabs() has attached its #tabModels
    # listener before we click. Otherwise the click lands on a button
    # with no handler, setTab('models') never runs, and the pane stays
    # hidden (issue #19).
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#paneModels", state="visible", timeout=3000)


def test_playground_tab(page, admin_url):
    page.goto(admin_url, wait_until="load")
    page.click("#tabPlayground")
    page.wait_for_selector("#panePlayground", state="visible", timeout=3000)
    # Model dropdown populated from /admin/api/playground/models. Boot
    # fans out several fetches at once, including install_status which
    # is the slow one — 10s window covers the cold-cache case.
    page.wait_for_function(
        "document.getElementById('playgroundModel').options.length > 0",
        timeout=10000,
    )


def test_static_assets_versioned(admin_url: str):
    """The index.html that comes off the wire stamps ``?v=<hash>`` onto
    every /admin/static/<file>.(css|js) URL."""
    r = httpx.get(admin_url, timeout=5.0)
    assert r.status_code == 200, r.text
    body = r.text
    # styles.css must carry a version stamp
    assert re.search(r"/admin/static/styles\.css\?v=[0-9a-f]{4,}", body), body[:1000]
    # main.js module
    assert re.search(r"/admin/static/main\.js\?v=[0-9a-f]{4,}", body), body[:1000]


def test_version_endpoint(admin_url: str):
    r = httpx.get(admin_url.rstrip("/") + "/api/version", timeout=5.0)
    assert r.status_code == 200
    body = r.json()
    assert body["git_sha"]
    assert body["built_at"]
    assert body["asset_hash"]


def _snapshot(page, name: str, browser_name: str) -> None:
    """Save a phone-viewport screenshot of the current page to a
    deterministic path under ``tests/e2e/snapshots/``. The sparklines
    region is masked because it re-renders every 2.5 s from live
    RAM/GPU readings — masking keeps the snapshot useful for human
    visual review without false diffs.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOT_DIR / f"{name}-{browser_name}.png"
    spark = page.locator("#hubSparklines")
    mask = [spark] if spark.count() > 0 else []
    page.screenshot(path=str(out), full_page=True, mask=mask)
    assert out.exists() and out.stat().st_size > 0, f"empty screenshot at {out}"


def test_hub_tab_phone_screenshot(page, admin_url, browser_name):
    page.set_viewport_size(PHONE_VIEWPORT)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#paneHub", state="visible", timeout=5000)
    # Let the first poll settle so the snapshot reflects the populated
    # UI rather than the "checking…" placeholder in the Hub card header.
    page.wait_for_function(
        "document.getElementById('hubLiveStatusText') && "
        "document.getElementById('hubLiveStatusText').textContent.indexOf('checking') === -1",
        timeout=8000,
    )
    _snapshot(page, "hub-390x844", browser_name)


def test_models_tab_phone_screenshot(page, admin_url, browser_name):
    page.set_viewport_size(PHONE_VIEWPORT)
    # See test_models_tab — wait_until="load" defeats the issue #19 race.
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#paneModels", state="visible", timeout=3000)
    _snapshot(page, "models-390x844", browser_name)


def test_playground_tab_phone_screenshot(page, admin_url, browser_name):
    page.set_viewport_size(PHONE_VIEWPORT)
    page.goto(admin_url, wait_until="load")
    page.click("#tabPlayground")
    page.wait_for_selector("#panePlayground", state="visible", timeout=3000)
    page.wait_for_function(
        "document.getElementById('playgroundModel').options.length > 0",
        timeout=10000,
    )
    _snapshot(page, "playground-390x844", browser_name)


def test_live_request_ring(admin_url: str):
    """A bad /v1/messages call must land in the live ring."""
    base = admin_url.rsplit("/admin/", 1)[0]
    # Make a deliberately-bad request — unknown model name.
    r = httpx.post(
        f"{base}/v1/messages",
        json={"model": "definitely-not-a-real-model", "messages": [{"role": "user", "content": "hi"}]},
        timeout=5.0,
    )
    assert r.status_code == 400
    # Give the ring a beat to ingest.
    time.sleep(0.2)
    r2 = httpx.get(admin_url.rstrip("/") + "/api/hub/requests/recent", timeout=5.0)
    body = r2.json()
    assert body["requests"], "request ring should not be empty"
    first = body["requests"][0]
    assert first["status"] == 400
    assert first["model"] == "definitely-not-a-real-model"
    assert first["latency_ms"] > 0


def test_live_requests_stream_rolls_forward(page, admin_url):
    """Regression guard for the live-stream pane.

    The bug this catches (issue #10 item 2): SSE seed fills the list,
    but subsequent frames never reach the UI, so the pane freezes at
    its initial snapshot. Boot the page, take a baseline count of
    #liveRequestsList rows, fire two distinct /v1/messages from this
    test process, and assert both rows show up within a short window.
    """
    base = admin_url.rsplit("/admin/", 1)[0]
    # wait_until="load" — consistent with test_models_tab (issue #19);
    # the later #tabModels click then can't race wireTabs() either.
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub", state="visible", timeout=5000)
    page.wait_for_selector("#liveRequestsList", state="attached", timeout=3000)
    # Let the EventSource open + the initial seed drain. We don't rely
    # on baseline counts because earlier tests in the same session may
    # have left records in the server-side ring.
    page.wait_for_timeout(700)

    # Use distinguishable model names so each marker is a unique row.
    marker_a = "e2e-rolls-forward-A"
    marker_b = "e2e-rolls-forward-B"
    for marker in (marker_a, marker_b):
        r = httpx.post(
            f"{base}/v1/messages",
            json={"model": marker, "messages": [{"role": "user", "content": "hi"}]},
            timeout=5.0,
        )
        assert r.status_code == 400, r.text

    # Both markers must arrive in the live list within a couple of seconds.
    page.wait_for_function(
        "(args) => {"
        "  const ul = document.getElementById('liveRequestsList');"
        "  if (!ul) return false;"
        "  const text = ul.textContent || '';"
        "  return text.indexOf(args.a) !== -1 && text.indexOf(args.b) !== -1;"
        "}",
        arg={"a": marker_a, "b": marker_b},
        timeout=4000,
    )

    def _marker_counts():
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
            [marker_a, marker_b],
        )

    counts = _marker_counts()
    assert counts[marker_a] == 1, f"marker_a duplicated on first delivery: {counts}"
    assert counts[marker_b] == 1, f"marker_b duplicated on first delivery: {counts}"

    # Bounce the SSE: switch away from Hub and back. main.js stops the
    # stream on tab-out and starts it again on tab-in — the server then
    # replays its 20-record seed, which used to duplicate every visible
    # row in the pane.
    page.click("#tabModels")
    page.wait_for_selector("#paneModels", state="visible", timeout=2000)
    page.wait_for_timeout(300)
    page.click("#tabHub")
    page.wait_for_selector("#paneHub", state="visible", timeout=2000)
    # Give the fresh EventSource time to receive the replayed seed.
    page.wait_for_timeout(900)
    counts_after = _marker_counts()
    assert counts_after[marker_a] == 1, (
        f"marker_a duplicated after SSE reconnect: {counts_after}"
    )
    assert counts_after[marker_b] == 1, (
        f"marker_b duplicated after SSE reconnect: {counts_after}"
    )
