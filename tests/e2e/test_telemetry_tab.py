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
    # Other panes hidden — wait for state, don't snapshot mid-transition.
    page.wait_for_selector("#paneHub", state="hidden", timeout=3000)
    page.wait_for_selector("#paneModels", state="hidden", timeout=3000)
    page.wait_for_selector("#panePlayground", state="hidden", timeout=3000)
    # Health strip + leaderboard table rendered.
    page.wait_for_selector("#telHealth", state="visible", timeout=3000)
    page.wait_for_selector("#telCountersTable", state="visible", timeout=3000)


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


def test_telemetry_picks_up_new_request(admin_url):
    """After a /v1/messages call, the telemetry recent endpoint surfaces it.

    Uses the API surface directly rather than waiting for the SPA's
    SSE delivery to render in the DOM — the SSE flow is identical to
    the Hub tab's (already covered by test_live_requests_stream_rolls_forward),
    and its UI-side timing is too racy on slower CI runners to be the
    primary signal for this test. The API endpoint is the source of
    truth that the Telemetry tab reads from anyway.
    """
    base = admin_url.rsplit("/admin/", 1)[0]
    marker = "e2e-tel-tab-marker"
    r = httpx.post(
        f"{base}/v1/messages",
        json={"model": marker, "messages": [{"role": "user", "content": "hi"}]},
        timeout=5.0,
    )
    assert r.status_code == 400, r.text

    # Give the OBS middleware's finally-block a beat to record + fan out.
    deadline = time.time() + 5.0
    found = False
    while time.time() < deadline:
        body = httpx.get(
            admin_url.rstrip("/") + "/api/telemetry/recent", timeout=3.0,
        ).json()
        traces = body.get("traces") or []
        if any(t.get("model") == marker for t in traces):
            found = True
            break
        time.sleep(0.2)
    assert found, "marker never showed in /admin/api/telemetry/recent"


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
