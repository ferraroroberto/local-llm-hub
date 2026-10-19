"""A machine that is off on purpose is dormant, and raises no alert (#765).

Two contracts, both pinned with route interception (invented machines, no real
fleet probed and no config written):

  * The Machines tab: a dormant machine is a neutral row sorted after the live
    ones, never counted in "N offline", named in the header's plain fact; one
    that answers again reads as reachable with a quiet hint (and no Wake) while
    the flag stays on.
  * Settings › Machines: each peer has a switch that PUTs the flag and updates
    the row's summary; the machine the hub runs on is not listed.
"""

from __future__ import annotations

import json

from tests.e2e._surface import surface

PHONE = {"width": 390, "height": 844}

STATUS = {"machines": [
    {"id": "tower", "display_name": "Tower", "icon": "monitor", "state": "self",
     "is_host": True, "role": "hub", "actions": {"wake": False}, "hub": None},
    # Intentionally off, listed ahead of a live machine to prove the sort.
    {"id": "attic", "display_name": "Attic", "icon": "server", "state": "dormant",
     "answering": False, "is_host": False, "role": "peer", "actions": {"wake": True}, "hub": None},
    {"id": "den", "display_name": "Den", "icon": "monitor", "state": "up",
     "is_host": False, "role": "peer", "uptime_seconds": 600,
     "actions": {"wake": False}, "hub": None},
    # Dormant but answering again (after a Wake).
    {"id": "loft", "display_name": "Loft", "icon": "server", "state": "dormant",
     "answering": True, "is_host": False, "role": "peer", "actions": {"wake": True}, "hub": None},
]}


def _json(route, body):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def test_dormant_machines_raise_no_alert_and_sort_last(browser, admin_url):
    context = browser.new_context(viewport=PHONE, has_touch=True)
    page = context.new_page()
    try:
        page.route("**/admin/api/machines/status", lambda r: _json(r, STATUS))
        page.goto(admin_url, wait_until="load")
        page.click("#tabMachines")
        page.wait_for_selector(".machine-row", state="visible", timeout=15000)

        ids = page.locator(".machine-row").evaluate_all("els => els.map(e => e.dataset.machineId)")
        assert ids == ["tower", "den", "attic", "loft"], ids  # live first, dormant after

        head = page.locator("#machinesHeadLine")
        assert head.inner_text() == "4 machines · 2 dormant"
        assert head.locator('[data-tone="danger"]').count() == 0, "no red alert"

        attic = page.locator('.machine-row[data-machine-id="attic"]')
        assert "Dormant" in attic.inner_text()
        assert attic.locator('.action-row-avatar[data-badge]').count() == 0, "no alive/down badge"
        assert attic.locator('[data-machine-action="wake"]').count() == 1

        loft = page.locator('.machine-row[data-machine-id="loft"]')
        assert "Reachable" in loft.inner_text()
        assert loft.locator('.action-row-avatar[data-badge="up"]').count() == 1
        assert loft.locator('[data-machine-action="wake"]').count() == 0, "it is up: no Wake"
        surface(page, "machines.dormant")
        loft.locator(".action-row-main").click()
        page.wait_for_selector("#machineSheet[open]", timeout=5000)
        assert "Switch Dormant off in Settings" in page.locator("#machineSheetBody").inner_text()
        surface(page, "machines.dormant-sheet")
    finally:
        context.close()


def test_settings_switch_marks_a_machine_dormant(browser, admin_url):
    context = browser.new_context(viewport=PHONE, has_touch=True)
    page = context.new_page()
    puts = []
    rows = [
        {"id": "attic", "display_name": "Attic", "role": "peer", "dormant": False},
        {"id": "den", "display_name": "Den", "role": "peer", "dormant": False},
    ]

    def _dormant(route):
        req = route.request
        if req.method == "PUT":
            body = json.loads(req.post_data)
            puts.append((req.url.rsplit("/", 2)[-2], body))
            return _json(route, {"ok": True, "id": "attic", "dormant": body["dormant"], "changed": True})
        _json(route, {"machines": rows})

    try:
        page.route("**/admin/api/machines/dormant", _dormant)
        page.route("**/admin/api/machines/attic/dormant", _dormant)
        page.goto(admin_url, wait_until="load")
        page.click("#paneHub .home-settings")
        assert page.inner_text("#dormantSummary") == "None dormant"
        page.click('#settingsDialog .settings-link[data-settings="machines"] .action-row-main')

        items = page.locator("#dormantList .startup-row")
        assert items.count() == 2 and "Attic" in items.first.inner_text()
        assert "Mark as intentionally off" in items.first.inner_text()

        switch = items.first.locator('button[role="switch"]')
        assert switch.get_attribute("aria-checked") == "false"
        switch.click()
        page.wait_for_function(
            "() => document.querySelector('#dormantList button[role=switch]').getAttribute('aria-checked') === 'true'"
        )
        assert puts == [("attic", {"dormant": True})], puts
        assert page.inner_text("#dormantSummary") == "1 of 2 dormant"
        surface(page, "settings.machines-dormant")
    finally:
        context.close()
