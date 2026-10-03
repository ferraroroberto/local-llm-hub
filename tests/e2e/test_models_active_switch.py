"""The Models card's "Active models only" switch shows its real state (#647).

The vendored switch paints state with the ``.on`` class (track + thumb) and
``aria-checked``. Both must follow the filter on load and on every toggle, and
the choice must survive a reload. The markup ships as ON, so a page whose JS
does not drive the class (a stale module under fresh markup) looks ON forever.
"""

from __future__ import annotations

from playwright.sync_api import expect

_SWITCH = "#modelsActiveToggle"
_STORE_KEY = "llmhub.models.activeOnly"


def _track(page) -> str:
    return page.evaluate(
        "sel => getComputedStyle(document.querySelector(sel)).backgroundColor", _SWITCH
    )


def _wait_track_not(page, colour: str) -> None:
    page.wait_for_function(
        "([sel, c]) => getComputedStyle(document.querySelector(sel)).backgroundColor !== c",
        arg=[_SWITCH, colour],
        timeout=2000,
    )


def _open(page, admin_url):
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector(_SWITCH, state="visible", timeout=5000)


def _assert_state(page, on: bool, on_track: str):
    """Class, aria-checked and the settled track colour all say ``on``.

    The track animates (0.15s transition), so the colour is awaited with the
    auto-retrying matcher rather than read once.
    """
    switch = page.locator(_SWITCH)
    expect(switch).to_have_attribute("aria-checked", "true" if on else "false")
    if on:
        expect(switch).to_have_class("toggle on")
        expect(switch).to_have_css("background-color", on_track)
    else:
        expect(switch).to_have_class("toggle")
        _wait_track_not(page, on_track)


def test_active_switch_follows_the_filter_and_persists(page, admin_url):
    page.add_init_script(
        f"try {{ if (!sessionStorage.getItem('seeded')) {{ localStorage.removeItem('{_STORE_KEY}');"
        " sessionStorage.setItem('seeded', '1'); } } catch (_) {}"
    )
    _open(page, admin_url)
    on_track = _track(page)
    _assert_state(page, True, on_track)

    page.click(_SWITCH)
    _assert_state(page, False, on_track)

    page.click(_SWITCH)
    _assert_state(page, True, on_track)

    page.click(_SWITCH)  # off, then reload: the choice persists
    _assert_state(page, False, on_track)
    page.reload(wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector(_SWITCH, state="visible", timeout=5000)
    _assert_state(page, False, on_track)
