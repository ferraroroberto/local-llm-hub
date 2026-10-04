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
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabModels")
    page.wait_for_selector("#modelsList .app-item", state="attached", timeout=10000)

    assert page.locator('#modelsList .app-icons .icon-btn[data-act="ping"]').count() == 0
    items = page.locator('#modelsList .row-menu-item[data-act="ping"]')
    assert items.count() > 0, "no row offers Ping from its More actions menu"
    for i in range(items.count()):
        assert (items.nth(i).text_content() or "").strip() == "Ping"


def test_header_opens_otel_with_a_chart_glyph(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#paneHub .home-head", state="attached")
    glyphs = page.evaluate(
        """() => [...document.querySelectorAll('.home-head [data-open-tab="telemetry"] use')]
            .map(u => u.getAttribute('href'))"""
    )
    assert glyphs, "no header button opens the OTel tab"
    assert set(glyphs) == {"#i-chart-column"}, glyphs


def test_header_otel_button_is_labelled_in_words(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#paneHub .home-head", state="attached")
    labels = page.evaluate(
        """() => [...document.querySelectorAll('.home-head [data-open-tab="telemetry"]')]
            .map(b => (b.textContent || '').trim())"""
    )
    assert labels, "no header button opens the OTel tab"
    assert set(labels) == {"Traces"}, labels
