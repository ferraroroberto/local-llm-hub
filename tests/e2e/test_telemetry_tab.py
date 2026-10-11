"""The OTel pane is gone and every way in opens Hub Activity (issue #4; #741).

The pane's functions live in Hub (the Langfuse sheet, the Activity feed and
its trace sheet, By model) and in Code (Sub-agents (OTel)). What stays here is
the routing contract: the nav holds exactly five tabs with no hidden sixth
entry, no page header carries a Traces button, and a `#telemetry` link or a
stored `telemetry` tab value opens Hub with the Activity feed in view. An
unknown stored tab falls back to Hub without an error.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("admin_url")

PHONE_VIEWPORT = {"width": 390, "height": 844}

_ACTIVITY_IN_VIEW = """() => {
    const r = document.getElementById('activityCard').getBoundingClientRect();
    return !document.getElementById('paneHub').hidden && r.top < window.innerHeight && r.bottom > 0;
}"""


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
    page.on("pageerror", lambda exc: errs.append(str(exc)))
    yield
    if errs:
        raise AssertionError("console errors: " + " | ".join(errs))


def test_telemetry_link_opens_hub_activity(page, admin_url):
    page.set_viewport_size(PHONE_VIEWPORT)
    page.goto(admin_url.rstrip("/") + "/#telemetry", wait_until="load")
    page.wait_for_selector("#paneHub", state="visible", timeout=5000)
    page.wait_for_function(_ACTIVITY_IN_VIEW, timeout=5000)
    # The fragment is consumed, so a reload lands on Hub's top as usual.
    assert page.evaluate("location.hash") == ""
    # Five tabs, no hidden sixth entry, no pane and no header Traces button.
    assert page.locator("nav.tabs [data-tab]").count() == 5
    assert page.locator("#tabTelemetry, #paneTelemetry, [data-tab='telemetry']").count() == 0
    assert page.locator(".home-head [data-open-tab]").count() == 0
    # The fragment works on an open page too.
    page.click("#tabModels")
    page.evaluate("location.hash = '#telemetry'")
    page.wait_for_function(_ACTIVITY_IN_VIEW, timeout=5000)


def test_stored_telemetry_tab_opens_hub_activity(page, admin_url):
    page.set_viewport_size(PHONE_VIEWPORT)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.evaluate("localStorage.setItem('llmhub.tab', 'telemetry')")
    page.reload(wait_until="load")
    page.wait_for_selector("#paneHub", state="visible", timeout=5000)
    page.wait_for_function(_ACTIVITY_IN_VIEW, timeout=5000)
    # Any other unknown value falls back to Hub, quietly.
    page.evaluate("localStorage.setItem('llmhub.tab', 'no-such-tab')")
    page.reload(wait_until="load")
    page.wait_for_selector("#paneHub", state="visible", timeout=5000)
