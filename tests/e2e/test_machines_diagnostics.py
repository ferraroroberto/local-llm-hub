"""End-to-end tests for the Machines-tab diagnostics drill-in (issue #315).

The regression these exist for: the dialog rendered as a bare header strip on
a phone, with no visible body. Two causes, both invisible to a desktop check —

  * ``machines.css`` loads *after* the vendored ``modal.css`` and re-declared
    ``max-height`` on the dialog at equal specificity, silently overriding the
    safe-area-aware mobile value in modal.css's own coarse-pointer block;
  * the card was forced to ``height: 100%`` with the body as an inner flex
    scroller, fighting the vendored contract (the *dialog* is the scroller).
    Against an indefinite parent height the body collapsed to ~nothing.

So these run at a phone viewport **with touch emulation**, which is what makes
``(pointer: coarse) and (max-width: 520px)`` match and exercises the real
mobile branch. A desktop-sized run cannot catch either bug.
"""

from __future__ import annotations

import pytest

PHONE_VIEWPORT = {"width": 390, "height": 844}


@pytest.fixture()
def phone_page(browser, admin_url):
    """A touch-enabled phone context — ``has_touch`` is what flips the CSS
    ``pointer: coarse`` media branch that the vendored modal keys its mobile
    anchoring and insets on."""
    context = browser.new_context(
        viewport=PHONE_VIEWPORT, has_touch=True, is_mobile=False,
    )
    page = context.new_page()
    page.set_default_timeout(15000)
    yield page
    context.close()


def _open_diagnostics(page, admin_url):
    page.goto(admin_url, wait_until="load")
    page.click("#tabMachines")
    page.wait_for_selector("#paneMachines", state="visible", timeout=5000)
    page.wait_for_selector(".diag-entry", state="visible", timeout=15000)
    page.click(".diag-entry")
    page.wait_for_selector("#diagDialog[open]", timeout=5000)
    # Wait for the *ready* render, not the first paint: showModal() paints a
    # loading empty-state immediately, and measuring that instead of the real
    # body is how this helper first fooled itself into a false failure.
    page.wait_for_selector("#diagBody .diag-capture", state="visible", timeout=15000)


def test_diagnostics_dialog_has_a_visible_body_on_a_phone(phone_page, admin_url):
    """The bug in one assertion: the dialog opened, but the body had no height,
    so all the user saw was the title bar."""
    page = phone_page
    _open_diagnostics(page, admin_url)

    body = page.locator("#diagBody")
    assert body.is_visible(), "diagnostics body is not visible on a phone viewport"
    box = body.bounding_box()
    assert box is not None and box["height"] > 200, (
        f"diagnostics body collapsed to {box and box['height']}px — the dialog "
        "is rendering as a bare header strip"
    )
    # The card must be taller than its header alone.
    card = page.locator(".machines-diag-card").bounding_box()
    header = page.locator(".machines-diag-card .detail-header").bounding_box()
    assert card["height"] > header["height"] * 2, "card is header-only"


def test_diagnostics_dialog_defers_to_the_vendored_scroller(phone_page, admin_url):
    """The card must not re-plumb the vendored shell: modal.css scrolls the
    dialog and lets the card flow naturally. A flex card with an inner
    overflow body is what collapsed on iOS."""
    page = phone_page
    _open_diagnostics(page, admin_url)

    card_display = page.evaluate(
        "getComputedStyle(document.querySelector('.machines-diag-card')).display"
    )
    body_overflow = page.evaluate(
        "getComputedStyle(document.getElementById('diagBody')).overflowY"
    )
    assert card_display != "flex", "card re-introduced the inner flex scroller"
    assert body_overflow != "auto", "body re-introduced its own scroller"
    assert page.evaluate(
        "(() => {const d=document.getElementById('diagDialog');"
        " return getComputedStyle(d).overflowY;})()"
    ) == "auto", "the vendored dialog should still be the scroller"


def test_diagnostics_dialog_does_not_override_mobile_max_height(phone_page, admin_url):
    """machines.css must not re-declare max-height on the dialog: it loads
    after modal.css at equal specificity, so doing so silently replaces the
    safe-area-aware mobile value. Asserted as an effect — the dialog must be
    bounded by the vendored coarse-pointer formula, which reserves space-lg +
    gap below the viewport rather than a flat 92%."""
    page = phone_page
    _open_diagnostics(page, admin_url)

    max_h = page.evaluate(
        "parseFloat(getComputedStyle(document.getElementById('diagDialog')).maxHeight)"
    )
    vh = page.evaluate("window.innerHeight")
    flat_92 = vh * 0.92
    assert max_h < vh, "dialog is not bounded by the viewport"
    assert abs(max_h - flat_92) > 1, (
        f"dialog max-height ({max_h}px) matches a flat 92vh — machines.css is "
        "overriding the vendored mobile safe-area rule again"
    )


def test_diagnostics_dialog_never_widens_the_page(phone_page, admin_url):
    """Wide content (the app/process/port tables) must scroll inside its own
    container; the page itself must never scroll sideways."""
    page = phone_page
    _open_diagnostics(page, admin_url)

    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    ), "the page scrolls horizontally with the diagnostics dialog open"
    assert page.evaluate(
        "[...document.querySelectorAll('.diag-table-wrap')]"
        ".every(el => getComputedStyle(el).overflowX === 'auto')"
    ), "a data table is not inside its own horizontal scroller"


def test_diagnostics_entry_row_only_on_this_machine(phone_page, admin_url):
    """A capture runs inside *this* hub's process, so offering the row on a
    peer card would be a button that cannot do what it says."""
    page = phone_page
    page.goto(admin_url, wait_until="load")
    page.click("#tabMachines")
    page.wait_for_selector(".machine-card", state="visible", timeout=15000)
    page.wait_for_selector(".diag-entry", state="visible", timeout=15000)
    entries = page.evaluate(
        "[...document.querySelectorAll('.machine-card')].map(c => "
        "({self: c.querySelector('.hub-live-status.accent') !== null,"
        "  hasDiag: c.querySelector('.diag-entry') !== null}))"
    )
    assert entries, "no machine cards rendered"
    for card in entries:
        assert card["hasDiag"] == card["self"], (
            "the diagnostics row must appear on the active host's card only"
        )
