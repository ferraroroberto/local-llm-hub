"""Layout contracts that hold on every tab.

* The page header is as tall as a collapsed card and stays inside a phone's
  width; its live line is never the line the phone drops.
* Large text scales every tab's title without overflowing, at phone and desktop
  width in both themes.
* Code text sits on the design system's recipes: a listing on the card surface
  with a hairline, an inline chip on neutral-soft.
* Navigation routes: a ``#telemetry`` link or a stored ``telemetry`` tab opens
  Hub with the Activity feed in view; an unknown stored tab falls back to Hub.
"""

from __future__ import annotations

import pytest

from tests.e2e._flow import DESKTOP, PHONE, fresh_page

# tab button id -> pane id (OTel is reached from the header, not the nav).
_TABS = {
    "#tabHub": "#paneHub",
    "#tabModels": "#paneModels",
    "#tabPlayground": "#panePlayground",
    "#tabCodeUsage": "#paneCodeUsage",
    "#tabMachines": "#paneMachines",
}

_ACTIVITY_IN_VIEW = """() => {
    const r = document.getElementById('activityCard').getBoundingClientRect();
    return !document.getElementById('paneHub').hidden && r.top < window.innerHeight && r.bottom > 0;
}"""

_MEASURE = """
() => {
  const probe = document.createElement('div');
  probe.innerHTML =
    '<pre class="logpane">listing</pre>' +
    '<ul class="cld-sessions"><li><div class="cld-sess-meta">' +
    '<code>0123abcd</code></div></li></ul>';
  document.body.appendChild(probe);
  const px = (el, p) => parseFloat(getComputedStyle(el)[p]);
  const rgb = (el, p) => getComputedStyle(el)[p];
  const swatch = (token) => {
    const s = document.createElement('i');
    s.style.background = `var(${token})`;
    document.body.appendChild(s);
    const v = getComputedStyle(s).backgroundColor;
    s.remove();
    return v;
  };
  const pre = probe.querySelector('.logpane');
  const meta = probe.querySelector('.cld-sess-meta');
  const code = probe.querySelector('code');
  const out = {
    preSize: px(pre, 'fontSize'),
    preBg: rgb(pre, 'backgroundColor'),
    preBorder: px(pre, 'borderTopWidth'),
    codeSize: px(code, 'fontSize'),
    metaSize: px(meta, 'fontSize'),
    codeBg: rgb(code, 'backgroundColor'),
    card: swatch('--card'),
    cardOff: swatch('--card-off'),
    neutralSoft: swatch('--neutral-soft'),
  };
  probe.remove();
  return out;
}
"""


def _height(page, selector: str) -> float:
    return page.evaluate("sel => document.querySelector(sel).getBoundingClientRect().height", selector)


def _headers_match_a_collapsed_card_and_fit_the_phone(page, admin_url):
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub .home-head", state="visible", timeout=5000)

    # A closed disclosure card's height (--row-md inside two 1px borders) is the
    # row geometry the header must match.
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
    # Machines names its state on the phone too, on one line.
    line = page.locator("#machinesHeadLine")
    assert line.is_visible(), "the Machines header line is hidden on a phone"
    assert page.evaluate(
        "() => getComputedStyle(document.getElementById('machinesHeadLine')).whiteSpace"
    ) == "nowrap"


def _title_px(page, pane: str) -> float:
    return page.evaluate(
        "pane => parseFloat(getComputedStyle(document.querySelector(pane + ' .home-title')).fontSize)",
        pane,
    )


def _pick_size(page, size: str) -> None:
    page.click("#tabHub")
    page.click("#paneHub .home-settings")
    page.click(f"#textSizeControl [data-textsize='{size}']")
    page.click("#settingsCloseBtn")


def _large_text_scales_every_tab_without_overflow(page, admin_url):
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub .home-settings", state="visible", timeout=5000)
    for viewport in (PHONE, DESKTOP):
        for theme in ("light", "dark"):
            page.set_viewport_size(viewport)
            page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
            _pick_size(page, "default")
            default = {}
            for tab, pane in _TABS.items():
                page.click(tab)
                page.wait_for_selector(f"{pane} .home-head", state="visible", timeout=5000)
                default[pane] = _title_px(page, pane)

            _pick_size(page, "large")
            for tab, pane in _TABS.items():
                page.click(tab)
                page.wait_for_selector(f"{pane} .home-head", state="visible", timeout=5000)
                assert _title_px(page, pane) == pytest.approx(default[pane] * 18 / 16), (pane, viewport, theme)
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                assert overflow <= 0, f"{pane} overflows by {overflow}px at Large ({viewport}, {theme})"


def _code_text_follows_the_recipes(page, admin_url):
    """A listing is 13px on the ``card`` surface with a hairline; an inline chip
    is 0.875em of the line around it on ``neutral-soft``. Neither may sit on
    ``--card-off``, which is true black inside a dark card."""
    page.goto(admin_url, wait_until="load")
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        m = page.evaluate(_MEASURE)
        assert m["preSize"] == 13.0, theme
        assert m["preBg"] == m["card"], theme
        assert m["preBg"] != m["cardOff"], theme
        assert m["preBorder"] == 1.0, theme
        # The meta line is body-sm, so the chip never drops under 11px.
        assert m["metaSize"] == 14.0, theme
        assert m["codeSize"] == pytest.approx(m["metaSize"] * 0.875, abs=0.01), theme
        assert m["codeBg"] == m["neutralSoft"], theme
        assert m["codeBg"] != m["cardOff"], theme


def _a_telemetry_link_opens_hub_activity(page, admin_url):
    page.goto(admin_url.rstrip("/") + "/#telemetry", wait_until="load")
    page.wait_for_selector("#paneHub", state="visible", timeout=5000)
    page.wait_for_function(_ACTIVITY_IN_VIEW, timeout=5000)
    # The fragment is consumed, so a reload lands on Hub's top as usual.
    assert page.evaluate("location.hash") == ""
    # Five tabs, no hidden sixth entry, no pane and no header Traces button.
    assert page.locator("nav.tabs [data-tab]").count() == 5
    assert page.locator("#tabTelemetry, #paneTelemetry, [data-tab='telemetry']").count() == 0
    assert page.locator(".home-head [data-open-tab]").count() == 0
    # The fragment works on an open page too.
    page.click("#tabModels")
    page.evaluate("location.hash = '#telemetry'")
    page.wait_for_function(_ACTIVITY_IN_VIEW, timeout=5000)


def _a_stored_telemetry_tab_opens_hub_activity(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.evaluate("localStorage.setItem('llmhub.tab', 'telemetry')")
    page.reload(wait_until="load")
    page.wait_for_selector("#paneHub", state="visible", timeout=5000)
    page.wait_for_function(_ACTIVITY_IN_VIEW, timeout=5000)
    # Any other unknown value falls back to Hub, quietly.
    page.evaluate("localStorage.setItem('llmhub.tab', 'no-such-tab')")
    page.reload(wait_until="load")
    page.wait_for_selector("#paneHub", state="visible", timeout=5000)


def test_layout_holds_on_every_tab(browser, admin_url):
    with fresh_page(browser, viewport=PHONE) as page:
        _headers_match_a_collapsed_card_and_fit_the_phone(page, admin_url)
    with fresh_page(browser) as page:
        _large_text_scales_every_tab_without_overflow(page, admin_url)
    with fresh_page(browser) as page:
        _code_text_follows_the_recipes(page, admin_url)
    with fresh_page(browser, console_errors=True, page_errors=True, viewport=PHONE) as page:
        _a_telemetry_link_opens_hub_activity(page, admin_url)
    with fresh_page(browser, console_errors=True, page_errors=True, viewport=PHONE) as page:
        _a_stored_telemetry_tab_opens_hub_activity(page, admin_url)
