"""End-to-end tests for the SPA Telemetry tab (issue #4).

Boots the hub with OTel disabled (set in tests/e2e/conftest.py) so the
tab renders against the no-OTel codepath — same path the user sees
before they start the Langfuse stack the first time.

Checks: tab is reachable, health strip renders without throwing, the
leaderboard table is present, the live-trace ring shows at least one
row after a /v1/messages call.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("admin_url")

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
PHONE_VIEWPORT = {"width": 390, "height": 844}


@pytest.fixture(autouse=True)
def _no_console_errors(page):
    errs = []

    def _on_console(msg):
        if msg.type != "error":
            return
        text = msg.text
        if "icon-180" in text or "icon-512" in text:
            return
        errs.append(text)

    page.on("console", _on_console)
    yield
    if errs:
        raise AssertionError("console errors: " + " | ".join(errs))


def test_telemetry_tab_loads(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#tabTelemetry", state="visible", timeout=5000)
    page.click("#tabTelemetry")
    page.wait_for_selector("#paneTelemetry", state="visible", timeout=3000)
    # Other panes hidden.
    assert page.is_hidden("#paneHub")
    assert page.is_hidden("#paneModels")
    assert page.is_hidden("#panePlayground")
    # Health strip + leaderboard table rendered.
    assert page.is_visible("#telHealth")
    assert page.is_visible("#telCountersTable")


def test_telemetry_health_renders(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabTelemetry")
    page.wait_for_selector("#paneTelemetry", state="visible", timeout=3000)
    # Wait for the first health poll to land (initial state is "checking…").
    page.wait_for_function(
        "document.getElementById('telHealthText') && "
        "document.getElementById('telHealthText').textContent.indexOf('checking') === -1",
        timeout=8000,
    )
    # OTel is disabled in the e2e hub (conftest), so the chip says so.
    state = page.text_content("#telOtelState")
    assert state in ("on", "off"), state


def test_telemetry_picks_up_new_request(page, admin_url):
    """After a /v1/messages call, the trace ring on the Telemetry tab
    shows a row for it. SSE drives this — same pattern as the Hub tab.

    Timing note: unlike the Hub tab (which opens its SSE on boot), the
    Telemetry tab opens its EventSource only after the tab-switch
    callback fires. We wait until the stream is actually open before
    firing the marker request — without that the request can race
    ahead of the subscription and the dispatched record is dropped on
    the floor instead of reaching the client.
    """
    base = admin_url.rsplit("/admin/", 1)[0]
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabTelemetry")
    page.wait_for_selector("#paneTelemetry", state="visible", timeout=3000)
    page.wait_for_selector("#telTracesList", state="attached", timeout=3000)
    # Wait until the EventSource is actually OPEN (readyState === 1)
    # rather than picking a magic-number sleep that's racy on slow CI
    # runners.
    page.wait_for_function(
        "() => window.__telStream && window.__telStream.readyState === 1",
        timeout=5000,
    )

    marker = "e2e-tel-tab-marker"
    r = httpx.post(
        f"{base}/v1/messages",
        json={"model": marker, "messages": [{"role": "user", "content": "hi"}]},
        timeout=5.0,
    )
    assert r.status_code == 400, r.text

    page.wait_for_function(
        "(m) => {"
        "  const ul = document.getElementById('telTracesList');"
        "  return ul && (ul.textContent || '').indexOf(m) !== -1;"
        "}",
        arg=marker,
        timeout=8000,
    )


def test_telemetry_tab_phone_screenshot(page, admin_url, browser_name):
    page.set_viewport_size(PHONE_VIEWPORT)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabTelemetry")
    page.wait_for_selector("#paneTelemetry", state="visible", timeout=3000)
    page.wait_for_function(
        "document.getElementById('telHealthText') && "
        "document.getElementById('telHealthText').textContent.indexOf('checking') === -1",
        timeout=8000,
    )
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOT_DIR / f"telemetry-390x844-{browser_name}.png"
    page.screenshot(path=str(out), full_page=True)
    assert out.exists() and out.stat().st_size > 0
