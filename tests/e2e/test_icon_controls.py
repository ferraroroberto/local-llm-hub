"""Icon-only controls are conventional glyphs; the unusual action says it in words (#647, J-05).

The design review's judgment pass flags icon-only buttons whose glyph is not
a convention: the signal-bars Ping on every model row and the activity-pulse
button that opens OTel. Ping now lives in the row's "More actions" menu as a
text item, and the header opens OTel with a chart glyph. A chart glyph alone has
no fixed meaning, so that header button also carries the word "Traces" (J-05 again,
both judges of the two-judge run flagged it).
"""

from __future__ import annotations


def test_model_rows_ping_from_the_menu_not_a_bare_glyph(page, admin_url):
    # The Models list opens on Running (#737), which holds only rows whose
    # backend is up or adopted: on a clean runner (CI) none is, so the list is
    # empty and no row ever renders (#752). Ping is offered on every row, so
    # ask for All -- the stored scope preference (``llmhub.models.activeOnly``:
    # "false" = All) -- and the test no longer depends on what the host runs.
    page.add_init_script(
        "try { localStorage.setItem('llmhub.models.activeOnly', 'false'); } catch (_) {}"
    )
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabModels")
    try:
        page.wait_for_selector("#modelsList .model-row", state="attached", timeout=10000)
    except Exception as exc:  # noqa: BLE001 -- name the missing element, then re-raise
        scope = page.evaluate("localStorage.getItem('llmhub.models.activeOnly')")
        empty = page.locator("#modelsEmpty").text_content() or ""
        raise AssertionError(
            "no '#modelsList .model-row' rendered on the Models tab "
            f"(stored scope={scope!r}, empty-state={empty.strip()!r})"
        ) from exc

    # Not a visible verb on the row (#737: Start / Stop is its only one).
    assert page.locator('#modelsList .action-row-verb[data-act="ping"]').count() == 0
    # The menu is the vendored row-menu, mounted on <body> while open.
    offered = 0
    kebabs = page.locator("#modelsList .row-kebab")
    for i in range(kebabs.count()):
        kebabs.nth(i).click()
        page.wait_for_selector("body > .row-menu", state="visible", timeout=5000)
        ping = page.locator('body > .row-menu .row-menu-item[data-act="ping"]')
        if ping.count():
            offered += 1
            assert (ping.first.text_content() or "").strip() == "Ping"
        page.keyboard.press("Escape")
    assert offered > 0, "no row offers Ping from its More actions menu"


def test_every_header_holds_the_theme_toggle_and_the_gear_only(page, admin_url):
    """The Traces button is gone (#741): each pane header carries exactly the
    theme toggle and the Settings gear (design.md page-header trailingActions 2)."""
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#paneHub .home-head", state="attached")
    heads = page.evaluate(
        """() => [...document.querySelectorAll('.home-head')].map(h =>
            [...h.querySelectorAll('button')].map(b =>
                b.classList.contains('theme-toggle') ? 'theme' : b.classList.contains('home-settings') ? 'gear' : b.className))"""
    )
    assert len(heads) == 5, heads
    assert all(h == ["theme", "gear"] for h in heads), heads
