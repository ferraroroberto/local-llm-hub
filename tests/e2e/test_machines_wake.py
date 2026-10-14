"""End-to-end tests for the Machines rows: Wake-on-LAN (issue #356) and the
shared row + machine sheet they live on since #736.

Wake is UI over the landed ``POST /admin/api/machines/{id}/wake`` endpoint and
the ``actions.wake`` flag on ``GET /admin/api/machines/status`` (true only for
a MAC-equipped, non-hub host). These tests pin the *SPA* contract with route
interception, so no real fleet is ever probed:

  * a **down** (or dormant) machine whose row advertises ``actions.wake`` shows
    Wake as the row's one visible verb; a down machine without the flag does
    not (the MAC gate);
  * an **up** or **self** machine never shows it *even when* ``actions.wake``
    is true; its row face still says it is up (the avatar badge) without a
    success chip, and tapping it opens the machine sheet, whose Reboot and
    Shut down are the last rows and open the confirm dialog; a peer that runs
    its own hub shows its out-of-sync state with both builds and offers Sync;
    on a wide window the same body fills the detail pane instead;
  * clicking Wake fires the wake POST, is non-destructive (no confirm dialog,
    not a danger button), and surfaces a success toast.

Run at a touch-enabled phone viewport — the same coarse-pointer branch the rest
of the Machines tab is validated against.
"""

from __future__ import annotations

import json

import pytest

PHONE_VIEWPORT = {"width": 390, "height": 844}
WIDE_VIEWPORT = {"width": 1440, "height": 900}

# A fixed fleet spanning every branch of the Wake verb's show/hide rule and the
# peer-hub states the sheet carries (invented names and builds).
FAKE_STATUS = {
    "machines": [
        {
            # This host — never wakeable.
            "id": "tower", "display_name": "Tower", "icon": "monitor",
            "state": "self", "is_host": True, "runs_hub": True,
            "role": "hub", "actions": {"wake": False}, "hub": None,
        },
        {
            # A reachable peer that advertises wake capability but is UP — the
            # state gate must suppress the verb regardless of the flag. It runs
            # its own hub, on a different build.
            "id": "desktop", "display_name": "Desktop", "icon": "monitor",
            "state": "up", "is_host": False, "runs_hub": True,
            "role": "peer", "uptime_seconds": 7200,
            "actions": {"wake": True, "reboot": True, "shutdown": True, "ssh_terminal": True, "rdp": True},
            "hub": {"reachable": True, "git_sha_match": False,
                    "local_git_sha": "aaa1111", "remote_git_sha": "bbb2222"},
        },
        {
            # A powered-off, MAC-equipped peer whose hub is down too — the row
            # shows Wake, the sheet offers Start hub.
            "id": "mini", "display_name": "Mac Mini", "icon": "server",
            "state": "down", "is_host": False, "runs_hub": True,
            "role": "peer", "actions": {"wake": True},
            "hub": {"reachable": False, "git_sha_match": None,
                    "local_git_sha": None, "remote_git_sha": None},
        },
        {
            # Powered off but with no MAC on file — wake unavailable, so no verb.
            "id": "laptop", "display_name": "Laptop", "icon": "laptop",
            "state": "down", "is_host": False, "runs_hub": False,
            "role": "peer", "actions": {"wake": False}, "hub": None,
        },
        {
            # Dormant + wakeable — the other state that shows the verb.
            "id": "sat", "display_name": "Satellite", "icon": "server",
            "state": "dormant", "is_host": False, "runs_hub": False,
            "role": "peer", "actions": {"wake": True}, "hub": None,
        },
    ]
}


@pytest.fixture()
def phone_page(browser, admin_url):
    """A touch-enabled phone context — ``has_touch`` flips the ``pointer:
    coarse`` branch the Machines tab is styled against."""
    context = browser.new_context(
        viewport=PHONE_VIEWPORT, has_touch=True, is_mobile=False,
    )
    page = context.new_page()
    page.set_default_timeout(15000)
    yield page
    context.close()


def _install_status_route(page):
    """Serve the fixed machine status for every poll."""
    page.route(
        "**/admin/api/machines/status",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(FAKE_STATUS),
        ),
    )


def _open_machines(page, admin_url):
    _install_status_route(page)
    page.goto(admin_url, wait_until="load")
    page.click("#tabMachines")
    page.wait_for_selector("#paneMachines", state="visible", timeout=5000)
    page.wait_for_selector(".machine-row", state="visible", timeout=15000)


def _row(page, machine_id):
    return page.locator(f'.machine-row[data-machine-id="{machine_id}"]')


def _wake_btn(page, machine_id):
    return _row(page, machine_id).locator('[data-machine-action="wake"]')


def test_wake_button_on_a_down_mac_equipped_machine(phone_page, admin_url):
    """A powered-off, MAC-equipped peer offers the Wake verb on its row."""
    page = phone_page
    _open_machines(page, admin_url)

    btn = _wake_btn(page, "mini")
    assert btn.count() == 1, "the down, wakeable machine should show a Wake verb"
    assert btn.is_visible()
    # A glyph-only icon-button: the name and tooltip carry the verb and the row.
    assert (btn.inner_text() or "").strip() == ""
    assert btn.get_attribute("aria-label") == "Wake Mac Mini"
    assert btn.get_attribute("title") == "Wake Mac Mini"
    assert btn.get_attribute("data-track") == "machine-wake"
    # It reuses the power glyph from the vendored sprite (no bespoke SVG).
    assert btn.locator('use[href="#i-power"]').count() == 1
    # A dormant, wakeable machine shows it too.
    assert _wake_btn(page, "sat").count() == 1
    # Down is readable from the row face: the avatar's down badge and the word.
    assert _row(page, "mini").locator('.action-row-avatar[data-badge="down"]').count() == 1
    assert "Offline" in _row(page, "mini").inner_text()
    # The header line names the offline count (two machines are down).
    assert page.locator("#machinesHeadLine").inner_text().startswith("2 offline")


def test_no_wake_button_without_the_mac_flag(phone_page, admin_url):
    """A down machine with ``actions.wake`` false gets no verb (the MAC gate)."""
    page = phone_page
    _open_machines(page, admin_url)

    assert _wake_btn(page, "laptop").count() == 0


def test_no_wake_button_on_up_or_self_machines(phone_page, admin_url, browser):
    """The state gate is independent of the capability flag: an UP peer that
    advertises ``actions.wake`` still hides the verb, and so does the host. Its
    row says "up" with the avatar badge and no success chip; tapping it opens
    the sheet, which carries the peer hub's state and the danger rows last."""
    page = phone_page
    _open_machines(page, admin_url)

    assert _wake_btn(page, "desktop").count() == 0, (
        "an up machine must never show Wake, even when actions.wake is true"
    )
    assert _wake_btn(page, "tower").count() == 0, "the self/host row never shows Wake"
    up = _row(page, "desktop")
    assert up.locator('.action-row-avatar[data-badge="up"]').count() == 1
    assert "Online" not in page.locator("#paneMachines").inner_text(), "no green Online chip"
    assert up.locator(".chip").inner_text() == "out of sync"

    up.locator(".action-row-main").click()
    page.wait_for_selector("#machineSheet[open]", timeout=5000)
    sheet = page.locator("#machineSheetBody")
    assert "Out of sync" in sheet.inner_text()
    assert "aaa1111" in sheet.inner_text() and "bbb2222" in sheet.inner_text()
    assert sheet.locator('[data-machine-action="sync"]').count() == 1
    # Reboot and Shut down are the last rows, and Reboot opens the confirm.
    actions = sheet.locator("[data-machine-action]").evaluate_all(
        "els => els.map(e => e.dataset.machineAction)")
    assert actions[-2:] == ["reboot", "shutdown"], actions
    sheet.locator('[data-machine-action="reboot"]').click()
    page.wait_for_selector("#machinesConfirmDialog[open]", timeout=5000)
    page.click("#machinesConfirmCloseBtn")
    page.click("#machineSheetCloseBtn")

    # The down hub peer's sheet offers Start hub (bootstrap) instead of Sync.
    _row(page, "mini").locator(".action-row-main").click()
    page.wait_for_selector("#machineSheet[open]", timeout=5000)
    assert sheet.locator('[data-machine-action="bootstrap"]').count() == 1
    assert sheet.locator('[data-machine-action="sync"]').count() == 0

    # A wide window renders the same body in the detail pane, not a sheet.
    wide = browser.new_context(viewport=WIDE_VIEWPORT)
    try:
        wpage = wide.new_page()
        _open_machines(wpage, admin_url)
        _row(wpage, "desktop").locator(".action-row-main").click()
        detail = wpage.locator("#machinesDetail")
        detail.locator('[data-machine-id="desktop"]').first.wait_for(timeout=5000)
        assert detail.is_visible()
        assert wpage.locator("#machineSheet[open]").count() == 0
        assert detail.locator('[data-machine-action="terminal"]').count() == 1
        assert detail.locator('[data-machine-action="shutdown"]').count() == 1
        assert _row(wpage, "desktop").get_attribute("class").find("is-selected") != -1
    finally:
        wide.close()


def test_wake_is_non_destructive_and_posts(phone_page, admin_url):
    """Clicking Wake fires the wake POST with no confirm dialog (non-destructive),
    and the verb is not a danger button; a success toast confirms it."""
    page = phone_page
    _open_machines(page, admin_url)

    wake_calls = []
    page.route(
        "**/admin/api/machines/*/wake",
        lambda route: (
            wake_calls.append(route.request.url),
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": True, "sent": True}),
            ),
        )[-1],
    )

    btn = _wake_btn(page, "mini")
    # Non-destructive: not styled as a danger action.
    assert "danger" not in (btn.get_attribute("class") or "")

    def is_wake_post(req):
        return req.url.endswith("/admin/api/machines/mini/wake") and req.method == "POST"

    with page.expect_request(is_wake_post):
        btn.click()

    assert wake_calls, "clicking Wake did not POST to the wake endpoint"
    # No confirm dialog was opened — unlike reboot/shutdown.
    assert page.locator("#machinesConfirmDialog[open]").count() == 0, (
        "Wake must not open the destructive-action confirm dialog"
    )
    # Success toast surfaces the machine name.
    toast = page.locator("#toast")
    toast.wait_for(state="visible", timeout=5000)
    assert "Wake packet sent to Mac Mini" in toast.inner_text()
