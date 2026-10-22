"""A failed startup-profile toggle reverts to the saved value, not the value
captured when the row was built (audit #850, finding 1).

Sequence: toggle a switch on (PATCH succeeds) then off (PATCH fails). The
switch must land back on ON — the last *saved* state — never OFF, the value
it happened to render at on page load.
"""

from __future__ import annotations

import json

PROFILE = {"profile": {"docker": False}, "services": [{"id": "docker", "label": "Docker"}]}


def test_failed_toggle_reverts_to_last_saved_value(browser, admin_url):
    context = browser.new_context()
    page = context.new_page()
    calls = []

    def _patch(route):
        req = route.request
        if req.method != "PATCH":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(PROFILE))
            return
        body = json.loads(req.post_data)
        calls.append(body)
        if len(calls) == 1:
            # First toggle (off -> on) succeeds and is now the saved value.
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": True, "profile": {"docker": True}}),
            )
        else:
            # Second toggle (on -> off) fails: must revert to the saved "on",
            # not to the "off" the row rendered with at build time.
            route.fulfill(status=500, content_type="application/json", body=json.dumps({"detail": "boom"}))

    try:
        page.route("**/admin/api/startup-profile", _patch)
        page.goto(admin_url, wait_until="load")
        page.click("#paneHub .home-settings")
        page.click('#settingsDialog .settings-link[data-settings="startup"] .action-row-main')

        switch = page.locator("#startupList button[role='switch']").first
        assert switch.get_attribute("aria-checked") == "false"

        switch.click()
        page.wait_for_function(
            "() => document.querySelector(\"#startupList button[role='switch']\").getAttribute('aria-checked') === 'true'"
        )

        switch.click()
        # The failed PATCH must settle the switch back to "true" (last saved),
        # not "false" (the value it was built with).
        page.wait_for_function("() => !document.querySelector(\"#startupList button[role='switch']\").disabled")
        assert switch.get_attribute("aria-checked") == "true"
        assert len(calls) == 2
    finally:
        context.close()
