"""End-to-end tests for the Wake-on-LAN button on a machine card (issue #356).

The button is UI over the landed ``POST /admin/api/machines/{id}/wake`` endpoint
and the ``actions.wake`` flag on ``GET /admin/api/machines/status`` (true only
for a MAC-equipped, non-hub host). These tests pin the *SPA* contract with route
interception, so no real fleet is ever probed:

  * a **down** (or dormant) machine whose card advertises ``actions.wake`` shows
    the Wake button;
  * a down machine *without* the flag shows no Wake button (the MAC gate);
  * an **up** or **self** machine never shows it *even when* ``actions.wake`` is
    true (the state gate is independent of the capability flag);
  * clicking Wake fires the wake POST, is non-destructive (no confirm dialog,
    not a danger button), and surfaces a success toast.

Run at a touch-enabled phone viewport — the same coarse-pointer branch the rest
of the Machines tab is validated against — so the 2-up rail with the full-width
Wake row is exercised as it renders on a phone.
"""

from __future__ import annotations

import json

import pytest

PHONE_VIEWPORT = {"width": 390, "height": 844}

# A fixed fleet spanning every branch of the Wake button's show/hide rule.
FAKE_STATUS = {
    "machines": [
        {
            # This host — self/accent state, runs the hub. Never wakeable.
            "id": "tower", "display_name": "Tower", "icon": "monitor",
            "state": "self", "is_host": True, "runs_hub": True,
            "role": "hub", "actions": {"wake": False},
        },
        {
            # A reachable peer that happens to advertise wake capability but is
            # UP — the state gate must suppress the button regardless of the flag.
            "id": "desktop", "display_name": "Desktop", "icon": "monitor",
            "state": "up", "is_host": False, "runs_hub": False,
            "role": "peer", "actions": {"wake": True},
        },
        {
            # A powered-off, MAC-equipped peer — the one card that shows Wake.
            "id": "mini", "display_name": "Mac Mini", "icon": "server",
            "state": "down", "is_host": False, "runs_hub": False,
            "role": "peer", "actions": {"wake": True},
        },
        {
            # Powered off but with no MAC on file — wake unavailable, so no button.
            "id": "laptop", "display_name": "Laptop", "icon": "laptop",
            "state": "down", "is_host": False, "runs_hub": False,
            "role": "peer", "actions": {"wake": False},
        },
        {
            # Dormant + wakeable — the other state that shows the button.
            "id": "sat", "display_name": "Satellite", "icon": "server",
            "state": "dormant", "is_host": False, "runs_hub": False,
            "role": "peer", "actions": {"wake": True},
        },
    ]
}


@pytest.fixture()
def phone_page(browser, admin_url):
    """A touch-enabled phone context — ``has_touch`` flips the ``pointer:
    coarse`` branch the Machines rail is styled against."""
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
    page.wait_for_selector(".machine-card", state="visible", timeout=15000)


def _wake_btn(page, machine_id):
    return page.locator(
        f'.machine-card[data-machine-id="{machine_id}"] button[data-action="wake"]'
    )


def test_wake_button_on_a_down_mac_equipped_machine(phone_page, admin_url):
    """A powered-off, MAC-equipped peer offers the Wake action."""
    page = phone_page
    _open_machines(page, admin_url)

    btn = _wake_btn(page, "mini")
    assert btn.count() == 1, "the down, wakeable machine should show a Wake button"
    assert btn.is_visible()
    assert "Wake" in btn.inner_text()
    # It reuses the power glyph from the vendored sprite (no bespoke SVG).
    assert btn.locator('use[href="#i-power"]').count() == 1
    # A dormant, wakeable machine shows it too.
    assert _wake_btn(page, "sat").count() == 1


def test_no_wake_button_without_the_mac_flag(phone_page, admin_url):
    """A down machine with ``actions.wake`` false gets no button (the MAC gate)."""
    page = phone_page
    _open_machines(page, admin_url)

    assert _wake_btn(page, "laptop").count() == 0


def test_no_wake_button_on_up_or_self_machines(phone_page, admin_url):
    """The state gate is independent of the capability flag: an UP peer that
    advertises ``actions.wake`` still hides the button, and so does the host."""
    page = phone_page
    _open_machines(page, admin_url)

    assert _wake_btn(page, "desktop").count() == 0, (
        "an up machine must never show Wake, even when actions.wake is true"
    )
    assert _wake_btn(page, "tower").count() == 0, "the self/host card never shows Wake"


def test_wake_is_non_destructive_and_posts(phone_page, admin_url):
    """Clicking Wake fires the wake POST with no confirm dialog (non-destructive),
    and the button is not a danger button; a success toast confirms it."""
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
