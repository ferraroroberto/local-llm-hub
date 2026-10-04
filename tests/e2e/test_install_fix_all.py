"""The Health & install card offers "Fix all" only when something is fixable (#647, J-02).

The design review's judgment pass flags a screen whose one filled button is a
repair action shown before any check has run (or when nothing is wrong): the
most prominent control then is not the screen's main task. "Fix all" now appears
only once a check reports a missing or failed item that carries a fix.
"""

from __future__ import annotations

import json


def _status(checks):
    worst = "ok"
    for c in checks:
        if c["status"] in ("missing", "error"):
            worst = c["status"]
    return {"checks": checks, "worst_status": worst}


ALL_OK = _status([{"id": "venv", "label": "Virtual environment", "status": "ok", "detail": ""}])
ONE_MISSING = _status([
    {"id": "venv", "label": "Virtual environment", "status": "ok", "detail": ""},
    {"id": "dotenv", "label": "Env file", "status": "missing", "detail": "no .env",
     "fix_id": "create_env", "fix_label": "Create"},
])


def _open(page, admin_url, body):
    page.route(
        "**/admin/api/install/status",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        ),
    )
    page.goto(admin_url, wait_until="load")
    page.eval_on_selector("#installCard", "el => { el.open = true; }")
    page.wait_for_selector("#installRows .install-row", state="attached", timeout=10000)


def test_fix_all_is_hidden_when_nothing_needs_fixing(page, admin_url):
    _open(page, admin_url, ALL_OK)
    assert page.locator("#installFixAllBtn").is_hidden()
    assert page.locator("#installRefreshBtn").is_visible()


def test_fix_all_appears_when_a_check_has_a_fix(page, admin_url):
    _open(page, admin_url, ONE_MISSING)
    assert page.locator("#installFixAllBtn").is_visible()
