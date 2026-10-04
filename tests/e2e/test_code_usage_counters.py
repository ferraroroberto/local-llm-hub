"""The Code tab's four usage cards read as one aligned set (#692).

Stubs the summary so every card carries a big token figure, a cost and a delta,
plus Codex reasoning tokens (the figure that used to add a third line to the
output card). Pins, at phone and desktop width in both themes:

  * the four cards have the same height (within 1 px);
  * the big numbers share a right edge (inside their card) and use tabular numerals;
  * on a phone the cost and the delta sit on one row in every card;
  * there is no "incl. … reasoning" line on the cards;
  * token figures group thousands ("2,323.7M") — the shared ``fmtTok`` that the
    Hub, Models and OTel views also use.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.usefixtures("admin_url")

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}
PANE_TIMEOUT = 10000

_BODY = {
    "period": "today", "vendor": "all",
    "totals": {
        "requests": 233445, "input_tokens": 41_564_000_000,
        "output_tokens": 2_323_700_000, "cache_read_tokens": 981_200_000,
        "cache_creation_tokens": 0, "reasoning_output_tokens": 120_000_000,
        "input_cost": 1234.5, "output_cost": 321.0, "cache_read_cost": 88.2,
    },
    "prev_totals": {
        "requests": 200000, "input_tokens": 40_000_000_000,
        "output_tokens": 2_000_000_000, "cache_read_tokens": 900_000_000,
        "cache_creation_tokens": 0,
    },
    "daily": [], "by_model": [], "by_project": [], "by_vendor": [],
    "recent_sessions": [], "time_series": [],
    "agentsview": {"enabled": False, "reachable": False, "vendors": []},
}


def _open_code_tab(page, admin_url, viewport, theme):
    page.set_viewport_size(viewport)
    page.route(
        "**/api/code/usage/summary*",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(_BODY)
        ),
    )
    page.goto(admin_url, wait_until="domcontentloaded")
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)
    page.wait_for_function(
        "(document.getElementById('cldFreshness')?.textContent || '').startsWith('updated')",
        timeout=PANE_TIMEOUT,
    )
    page.wait_for_function(
        "document.getElementById('cldInputTok').textContent !== '—'",
        timeout=PANE_TIMEOUT,
    )


@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_usage_cards_are_equal_height_and_aligned(page, admin_url, viewport, theme):
    _open_code_tab(page, admin_url, viewport, theme)

    cards = page.evaluate(
        """() => [...document.querySelectorAll('#cldCounterGrid .cld-counter')].map(c => {
            const r = el => el.getBoundingClientRect();
            const text = el => { const g = document.createRange(); g.selectNodeContents(el); return g.getBoundingClientRect(); };
            const value = c.querySelector('.cld-counter-value');
            const cost = c.querySelector('.cld-counter-cost');
            const delta = c.querySelector('.cld-delta');
            return {
                height: r(c).height,
                valueInset: r(c).right - text(value).right,
                numerals: getComputedStyle(value).fontVariantNumeric,
                costTop: r(cost).top,
                deltaTop: r(delta).top,
                deltaShown: !delta.hidden,
                text: c.innerText,
            };
        })"""
    )
    assert len(cards) == 4
    heights = [c["height"] for c in cards]
    assert max(heights) - min(heights) <= 1, f"card heights differ: {heights}"
    # Side by side (desktop) the cards sit at different x, so the right edge is
    # measured from each card's own edge: the digits end the same distance in.
    insets = [c["valueInset"] for c in cards]
    assert max(insets) - min(insets) <= 1, f"number right edges differ: {insets}"
    for c in cards:
        assert "tabular-nums" in c["numerals"], c["numerals"]
        assert c["deltaShown"], "every stub card carries a delta"
        if viewport is PHONE:   # stacked cards: cost and delta share one row
            assert abs(c["costTop"] - c["deltaTop"]) <= 2, (
                f"cost and delta are not on one row: {c['costTop']} vs {c['deltaTop']}"
            )
        assert "reasoning" not in c["text"].lower(), c["text"]
    assert page.locator("#cldOutputReasoning").count() == 0


def test_token_figures_group_thousands(page, admin_url):
    _open_code_tab(page, admin_url, DESKTOP, "light")
    assert page.inner_text("#cldInputTok") == "41,564.0M"
    assert page.inner_text("#cldOutputTok") == "2,323.7M"
    assert page.inner_text("#cldCacheRead") == "981.2M"


@pytest.mark.parametrize(
    "n, expected",
    [
        (0, "—"), (999, "999"), (1_000, "1.0k"), (123_456, "123.5k"),
        (1_000_000, "1.0M"), (999_999_999, "1,000.0M"),
        (2_323_700_000, "2,323.7M"), (41_564_000_000, "41,564.0M"),
    ],
)
def test_fmt_tok_groups_the_integer_part(page, admin_url, n, expected):
    """``fmtTok`` is shared by the Hub, Models and OTel views, so its contract
    is checked on the function itself, not only through the Code tab."""
    page.goto(admin_url, wait_until="domcontentloaded")
    got = page.evaluate(
        "n => import('/admin/static/api.js').then(m => m.fmtTok(n))", n
    )
    assert got == expected
