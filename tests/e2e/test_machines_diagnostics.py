"""The Machines tab's diagnostics drill-in.

The regression these exist for: the dialog rendered as a bare header strip on a
phone, with no visible body. Two causes, both invisible to a desktop check:

  * ``machines.css`` loads *after* the vendored ``modal.css`` and re-declared
    ``max-height`` on the dialog at equal specificity, silently overriding the
    safe-area-aware mobile value in modal.css's own coarse-pointer block;
  * the card was forced to ``height: 100%`` with the body as an inner flex
    scroller, fighting the vendored contract (the *dialog* is the scroller).
    Against an indefinite parent height the body collapsed to ~nothing.

So the phases run at a phone viewport **with touch emulation**, which is what
makes ``(pointer: coarse) and (max-width: 520px)`` match and exercises the real
mobile branch. A desktop-sized run cannot catch either bug.
"""

from __future__ import annotations

from tests.e2e._flow import PHONE, fresh_page
from tests.e2e._surface import surface


def _open_machines(page, admin_url):
    page.goto(admin_url, wait_until="load")
    page.click("#tabMachines")
    page.wait_for_selector("#paneMachines", state="visible", timeout=5000)
    page.wait_for_selector(".diag-entry", state="visible", timeout=15000)


def _open_dialog(page):
    page.click(".diag-entry")
    page.wait_for_selector("#diagDialog[open]", timeout=5000)
    # Wait for the *ready* render, not the first paint: showModal() paints a
    # loading empty-state immediately, and measuring that instead of the real
    # body is how a helper like this fools itself into a false failure.
    page.wait_for_selector("#diagBody .diag-capture", state="visible", timeout=15000)


def _the_entry_is_one_row_for_this_machine(page, admin_url):
    """A capture runs inside *this* hub's process, so the entry is offered for the
    machine being viewed only: exactly one, its own row under the machine list,
    never on a machine row, naming the machine it is for."""
    _open_machines(page, admin_url)
    page.wait_for_selector(".machine-row", state="visible", timeout=15000)
    assert page.locator(".diag-entry").count() == 1, "one Diagnostics entry, for this machine"
    assert page.locator("#machinesList .diag-entry").count() == 0, (
        "the diagnostics row must not ride on a machine row")
    assert "This machine" in page.locator("#machinesDiagCard").inner_text()


def _the_dialog_has_a_body_the_vendored_scroller_owns(page, mark):
    # The bug in one assertion: the dialog opened, but the body had no height, so
    # all the user saw was the title bar.
    body = page.locator("#diagBody")
    assert body.is_visible(), "diagnostics body is not visible on a phone viewport"
    box = body.bounding_box()
    assert box is not None and box["height"] > 200, (
        f"diagnostics body collapsed to {box and box['height']}px: the dialog is rendering as a bare header strip")
    card = page.locator(".machines-diag-card").bounding_box()
    header = page.locator(".machines-diag-card .detail-header").bounding_box()
    assert card["height"] > header["height"] * 2, "card is header-only"
    mark("machines.diagnostics-dialog")

    # The card must not re-plumb the vendored shell: modal.css scrolls the dialog
    # and lets the card flow naturally. A flex card with an inner overflow body is
    # what collapsed on iOS.
    assert page.evaluate("getComputedStyle(document.querySelector('.machines-diag-card')).display") != "flex", (
        "card re-introduced the inner flex scroller")
    assert page.evaluate("getComputedStyle(document.getElementById('diagBody')).overflowY") != "auto", (
        "body re-introduced its own scroller")
    assert page.evaluate(
        "(() => {const d=document.getElementById('diagDialog'); return getComputedStyle(d).overflowY;})()"
    ) == "auto", "the vendored dialog should still be the scroller"

    # machines.css must not re-declare max-height on the dialog: it loads after
    # modal.css at equal specificity, so doing so silently replaces the
    # safe-area-aware mobile value. Asserted as an effect: the dialog is bounded by
    # the vendored coarse-pointer formula, not a flat 92%.
    max_h = page.evaluate("parseFloat(getComputedStyle(document.getElementById('diagDialog')).maxHeight)")
    vh = page.evaluate("window.innerHeight")
    assert max_h < vh, "dialog is not bounded by the viewport"
    assert abs(max_h - vh * 0.92) > 1, (
        f"dialog max-height ({max_h}px) matches a flat 92vh: machines.css is overriding the vendored mobile safe-area rule again")

    # Wide content (the app/process/port tables) scrolls inside its own container;
    # the page itself never scrolls sideways.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        "the page scrolls horizontally with the diagnostics dialog open")
    assert page.evaluate(
        "[...document.querySelectorAll('.diag-table-wrap')].every(el => getComputedStyle(el).overflowX === 'auto')"
    ), "a data table is not inside its own horizontal scroller"


def _a_poll_tick_keeps_unsaved_form_choices(page, admin_url):
    """The 5 s status poll rebuilt the whole body through innerHTML, so a user who
    picked "8 hours" and was a few seconds slow to press Start capture silently got
    the 1-hour default; the Settings <details> collapsed and the unsaved
    Snapshot-every / Keep-raw-samples picks were lost the same way.

    Two ticks, driven by a stubbed status: one that changes nothing (the DOM must
    be left alone) and one that changes the Settings markup (the body is repainted,
    and the choices carry across)."""
    db = {"bytes": 5 * 1024 * 1024}

    def status_handler(route):
        resp = route.fetch()
        body = resp.json()
        body.setdefault("settings", {})["db_size_bytes"] = db["bytes"]
        route.fulfill(response=resp, json=body)

    page.route("**/admin/api/diagnostics/status", status_handler)
    _open_machines(page, admin_url)
    _open_dialog(page)

    page.select_option("#diagDuration", "28800")
    page.select_option("#diagInterval", "60")
    page.click(".diag-settings-block summary")
    page.select_option("#diagScheduleHours", "48")
    page.select_option("#diagRetention", "365")
    # Tag the node: a repaint replaces it, so the tag disappears.
    page.evaluate("document.getElementById('diagDuration').dataset.tag = 'kept'")

    def choices():
        return page.evaluate(
            "({duration: diagDuration.value, interval: diagInterval.value,"
            " hours: diagScheduleHours.value, retention: diagRetention.value,"
            " open: document.querySelector('.diag-settings-block').open})"
        )

    expected = {"duration": "28800", "interval": "60", "hours": "48", "retention": "365", "open": True}

    # Tick 1: identical status -> identical markup -> nothing is rewritten.
    with page.expect_response(lambda r: r.url.endswith("/diagnostics/status"), timeout=15000):
        pass
    page.evaluate("new Promise(r => setTimeout(r, 300))")
    assert choices() == expected
    assert page.evaluate("document.getElementById('diagDuration').dataset.tag") == "kept"

    # Tick 2: the database grew -> the Settings markup changed -> repainted, and
    # the unsaved choices are carried across the repaint.
    db["bytes"] = 9 * 1024 * 1024
    page.wait_for_function(
        "document.querySelector('.diag-settings-block').innerText.includes('Database 9 MB')", timeout=15000)
    assert page.evaluate("document.getElementById('diagDuration').dataset.tag") is None, (
        "the body was not repainted, so the carry-across path was not exercised")
    assert choices() == expected


def test_machines_diagnostics_dialog(browser, admin_url):
    with fresh_page(browser, viewport=PHONE, has_touch=True) as page:
        _the_entry_is_one_row_for_this_machine(page, admin_url)
        _open_dialog(page)
        _the_dialog_has_a_body_the_vendored_scroller_owns(page, lambda name: surface(page, name))
    with fresh_page(browser, viewport=PHONE, has_touch=True) as page:
        _a_poll_tick_keeps_unsaved_form_choices(page, admin_url)
