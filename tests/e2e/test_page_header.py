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

    # A closed disclosure card's height (--row-md inside two 1px borders) is
    # the row geometry the header must match; Hub has no folded card since #741.
    row = page.evaluate("""() => {
        const probe = document.createElement('div');
        probe.style.height = 'calc(var(--row-md) + 2px)';
        document.body.appendChild(probe);
        const h = probe.getBoundingClientRect().height;
        probe.remove();
        return h;
    }""")

    for tab, pane in _TABS.items():
        page.click(tab)
        page.wait_for_selector(f"{pane} .home-head", state="visible", timeout=5000)
        header = _height(page, f"{pane} .home-head")
        assert abs(header - row) <= 1, f"{pane}: header {header}px vs collapsed card {row}px"


def test_every_header_stays_inside_the_phone_width(page, admin_url):
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
    # A live header line (rows.js setHeadLine, #736) is never the line the
    # phone drops: Machines names its state on the phone too, on one line.
    line = page.locator("#machinesHeadLine")
    assert line.is_visible(), "the Machines header line is hidden on a phone"
    assert page.evaluate(
        "() => getComputedStyle(document.getElementById('machinesHeadLine')).whiteSpace"
    ) == "nowrap"


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
