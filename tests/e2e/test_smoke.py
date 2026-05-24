"""End-to-end smoke tests on the /admin SPA.

What the gate proves:

  * Each tab pane (Hub / Models / Playground) loads without throwing a
    JavaScript console error.
  * The hub-control round-trip works: a /v1/messages request to a known-
    bad model lands as a row in the live-request ring (SSE arrives).
  * Static assets carry the ?v=<hash> stamp (cache-busting wired up).
  * GET /admin/api/version returns a non-empty git_sha + asset_hash.

Runs under Chromium *and* WebKit projections — WebKit catches the
iOS-Safari class of cache / SSE bugs Chromium will silently paper over.
"""

from __future__ import annotations

import re
import time

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("admin_url")


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
        # Tolerated noise — placeholder PNG icons get a 404 on Safari
        # because pytest-playwright's WebKit doesn't read the cached
        # asset hash from the manifest.
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
    # Hub tab is the default
    assert page.is_visible("#paneHub")
    assert page.is_hidden("#paneModels")
    assert page.is_hidden("#panePlayground")


def test_models_tab(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabModels")
    page.wait_for_selector("#paneModels", state="visible", timeout=3000)
    # Either some model cards rendered or the empty-state is shown.
    assert page.is_visible("#paneModels")


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
