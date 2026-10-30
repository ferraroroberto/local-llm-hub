""""Fix all" reports a per-fix failure instead of a blanket success toast
(audit #850, finding 2).

``POST /admin/api/install/fix-all`` always returns 200, recording each fix's
outcome in ``ran`` (``app_web/routers/install.py``). A failed repair must not
be announced with the same green "Fix-all complete." toast as a clean run.
"""

from __future__ import annotations

import json

STATUS = {
    "worst_status": "missing",
    "checks": [
        {"id": "dotenv", "label": "Env file", "status": "missing", "detail": "no .env",
         "fix_id": "create_env", "fix_label": "Create"},
    ],
}


def test_fix_all_with_a_failure_shows_an_error_toast(page, admin_url):
    page.route(
        "**/admin/api/install/status",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(STATUS)),
    )
    page.route(
        "**/admin/api/install/fix-all",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"ran": [{"fix_id": "create_env", "ok": False, "error": "boom"}]}),
        ),
    )
    page.goto(admin_url, wait_until="load")
    page.click("#paneHub .home-settings")
    page.click('#settingsDialog .settings-link[data-settings="health"] .action-row-main')
    page.wait_for_selector("#installFixAllBtn", state="visible", timeout=10000)

    page.click("#installFixAllBtn")
    page.wait_for_function("() => !document.getElementById('toast').hidden")
    toast = page.locator("#toast")
    assert "error" in (toast.get_attribute("class") or "")
    assert "create_env" in toast.inner_text()
    assert "boom" in toast.inner_text()
    # The wrench icon is part of the label and must survive the Running… swap.
    assert page.locator("#installFixAllBtn svg").count() == 1
