"""The Models list's Running · All control shows its real state (#647, #737).

Since #737 the scope is the vendored segmented control (a true peer set), not
the "Active models only" switch. It keeps the switch's stored preference
(``llmhub.models.activeOnly``: "true" = Running), so nobody loses their
choice; the raised segment (``aria-pressed``) must follow the filter on load
and on every tap, survive a reload, and the empty state must read right in
both positions.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

_SCOPE = "#modelsScope"
_STORE_KEY = "llmhub.models.activeOnly"

# One stopped model and nothing running: Running is empty, All is not.
FAKE_MODELS = {
    "models": [
        {
            "id": "stopped_row", "display_name": "stopped-row", "backend": "llama",
            "port": 8082, "aliases": [], "controllable": True, "ownership": "none",
            "pid": None, "reachable": False, "host": "tower",
        },
    ],
}


def _open(page, admin_url):
    page.route(
        "**/admin/api/models",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(FAKE_MODELS)
        ),
    )
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector(_SCOPE, state="visible", timeout=5000)


def _assert_scope(page, running: bool):
    seg = page.locator(_SCOPE)
    expect(seg.locator('[data-scope="running"]')).to_have_attribute("aria-pressed", "true" if running else "false")
    expect(seg.locator('[data-scope="all"]')).to_have_attribute("aria-pressed", "false" if running else "true")
    empty = page.locator("#modelsEmpty")
    if running:
        expect(empty).to_be_visible()
        expect(empty).to_contain_text("choose All")
        expect(page.locator('#modelsList .model-row[data-id="stopped_row"]')).to_have_count(0)
    else:
        expect(empty).to_be_hidden()
        expect(page.locator('#modelsList .model-row[data-id="stopped_row"]')).to_have_count(1)


def test_running_all_follows_the_filter_and_persists(page, admin_url):
    # A preference stored by the old switch ("false" = show everything) is honoured.
    page.add_init_script(
        f"try {{ if (!sessionStorage.getItem('seeded')) {{ localStorage.setItem('{_STORE_KEY}', 'false');"
        " sessionStorage.setItem('seeded', '1'); } } catch (_) {}"
    )
    _open(page, admin_url)
    _assert_scope(page, running=False)
    assert page.locator("#modelsActiveToggle").count() == 0, "the old switch is gone"

    page.click(f'{_SCOPE} [data-scope="running"]')
    _assert_scope(page, running=True)
    assert page.evaluate(f"localStorage.getItem('{_STORE_KEY}')") == "true"

    page.click(f'{_SCOPE} [data-scope="all"]')
    _assert_scope(page, running=False)

    page.click(f'{_SCOPE} [data-scope="running"]')  # Running, then reload: the choice persists
    page.reload(wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector(_SCOPE, state="visible", timeout=5000)
    _assert_scope(page, running=True)
