"""The page-header card matches the collapsed card rows (#647).

The vendored home-head is one row at the disclosure closed-summary geometry
(``--row-md``, 52px). If the token is missing the row collapses to its 34px
icon buttons and looks shorter than the cards beneath it.
"""

from __future__ import annotations

PHONE = {"width": 390, "height": 844}

# tab button id -> pane id
_TABS = {
    "#tabHub": "#paneHub",
    "#tabModels": "#paneModels",
    "#tabPlayground": "#panePlayground",
    "#tabCodeUsage": "#paneCodeUsage",
    "#tabMachines": "#paneMachines",
}


def _height(page, selector: str) -> float:
    return page.evaluate(
        "sel => document.querySelector(sel).getBoundingClientRect().height", selector
    )


def test_page_header_is_as_tall_as_a_collapsed_card(page, admin_url):
    page.set_viewport_size(PHONE)
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub .home-head", state="visible", timeout=5000)

    # A closed disclosure card's height is the row geometry the header must match.
    closed = "#paneHub details.card--collapsible:not([open])"
    page.wait_for_selector(closed, state="visible", timeout=5000)
    row = _height(page, closed)

    for tab, pane in _TABS.items():
        page.click(tab)
        page.wait_for_selector(f"{pane} .home-head", state="visible", timeout=5000)
        header = _height(page, f"{pane} .home-head")
        assert abs(header - row) <= 1, f"{pane}: header {header}px vs collapsed card {row}px"


def test_labelled_otel_button_keeps_every_header_inside_the_phone_width(page, admin_url):
    page.set_viewport_size(PHONE)
    page.goto(admin_url, wait_until="load")
    for tab, pane in _TABS.items():
        page.click(tab)
        page.wait_for_selector(f"{pane} .home-head", state="visible", timeout=5000)
        edges = page.evaluate(
            """pane => {
                const head = document.querySelector(pane + ' .home-head');
                const h = head.getBoundingClientRect();
                const right = Math.max(...[...head.querySelectorAll('button')]
                    .map(b => b.getBoundingClientRect().right));
                const st = head.querySelector('.status');
                const stub = st && getComputedStyle(st).display !== 'none'
                    ? st.getBoundingClientRect().width : null;
                return { overflow: head.scrollWidth - head.clientWidth, right, edge: h.right, stub };
            }""",
            pane,
        )
        assert edges["overflow"] <= 0, f"{pane}: header overflows by {edges['overflow']}px"
        assert edges["right"] <= edges["edge"], f"{pane}: a header button sits past the card edge"
        # A context line is either readable or gone, never a one-letter stub.
        assert edges["stub"] is None or edges["stub"] >= 60, (
            f"{pane}: context line squeezed to {edges['stub']}px"
        )


_PAINT = """els => els.map(el => {
    const s = getComputedStyle(el);
    return [s.backgroundColor, s.borderTopWidth];
})"""


def test_header_toggles_and_dialog_close_are_unpainted_glyphs(page, admin_url):
    """They are .icon-button's (project-scaffolding#339): a glyph on nothing at rest."""
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub .home-head", state="visible", timeout=5000)
    toggles = page.locator("#paneHub .home-head .home-toggle")
    closes = page.locator(".detail-close")
    assert toggles.count() == 2 and closes.count() >= 4
    for paint in toggles.evaluate_all(_PAINT) + closes.evaluate_all(_PAINT):
        assert paint == ["rgba(0, 0, 0, 0)", "0px"]
