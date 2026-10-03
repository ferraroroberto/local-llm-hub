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
