"""The Code tab: what the usage summary draws, and what switching it does.

Boots the hub with OTel disabled (set in ``tests/e2e/_isolated_hub.py``). The
test hub reads no usage history, so a phase that stubs nothing renders the
empty-state path of ``/admin/api/code/usage/summary``. Each phase runs in its
own browser context.

``test_code_usage_renders_the_summary`` reads the picture:
  * the four usage cards are one aligned set (same height, shared right edge,
    tabular numerals, cost and delta on one row on a phone) in both themes at
    phone and desktop width; token figures group thousands (the shared
    ``fmtTok`` that Hub and Models use too); an empty cost line draws nothing;
    the cards stack on a phone;
  * the trend is stacked vendor bars, each model in its vendor's hue, with a
    total tooltip, and the vendor table and opacity ranking hold on the
    functions;
  * no section reads empty before the first summary has loaded, every empty
    state names its control, and the first screen holds the period switch, the
    counters and the total cost on a phone and on a PC window;
  * a failed summary renders as an error, not a zero-usage day.

``test_code_usage_switching`` drives the controls:
  * re-tapping the active tab leaves one poll interval;
  * the period switch and the agent sheet switch the choice, every agent
    including a runtime AgentsView extra with its freshness, and the Sub-agents
    (OTel) source driven by the one period switch, never summed into All;
  * a switch shows the frosted "Loading usage…" toast until the data lands;
    rapid clicks fire one request for the final choice; a click after the
    request is out aborts the stale one; the background poll never shows the
    toast; a failed fetch shows the error toast and keeps the error banner.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tests.e2e._flow import (
    DESKTOP,
    PHONE,
    SIDE_BY_SIDE,
    fresh_page,
    fulfil_json,
    snapshot,
)
from tests.e2e._surface import surface

PANE_TIMEOUT = 10000


@pytest.fixture(scope="session", autouse=True)
def _usage_data_is_hermetic(admin_url):
    """Fail fast if the test hub is reading this machine's usage history.

    ``tests/e2e/_isolated_hub.py`` points every Code Usage source at an empty
    path. Left pointing at the host's real session history, the summary endpoint
    cold-scans it (about 40 s on a ~300k-record history, over half the suite's
    wall time) and the tab renders whoever's data is on the box. The all-time,
    all-vendor summary is the widest view, so zero requests there means no
    source leaked in.
    """
    base = admin_url.rstrip("/") + "/api/code/usage/summary"
    r = httpx.get(base, params={"period": "all", "vendor": "all"}, timeout=30.0)
    assert r.status_code == 200, f"usage summary failed: {r.text}"
    assert r.json()["totals"]["requests"] == 0, (
        "the e2e hub read real usage history; see _isolate_usage_data in tests/e2e/_isolated_hub.py"
    )


def _summary(period="today", vendor="all", *, totals, prev_totals=None, by_vendor=(),
             time_series=(), agentsview=None):
    body = {
        "period": period, "vendor": vendor, "totals": totals,
        "daily": [], "by_model": [], "by_project": [], "by_vendor": list(by_vendor),
        "recent_sessions": [], "time_series": list(time_series),
        "agentsview": agentsview or {"enabled": False, "reachable": False, "vendors": []},
    }
    if prev_totals is not None:
        body["prev_totals"] = prev_totals
    return body


def _await_first_summary(page):
    """Block until the tab's first summary poll has been fetched *and* drawn.

    ``#cldFreshness`` starts empty and only reads ``updated …`` once ``render``
    has run.
    """
    page.wait_for_function(
        "(document.getElementById('cldFreshness')?.textContent || '').startsWith('updated')",
        timeout=PANE_TIMEOUT,
    )


def _open_code_tab(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)


# --------------------------------------------------------------------------- #
# The picture
# --------------------------------------------------------------------------- #

# Every card carries a big token figure, a cost and a delta, plus Codex
# reasoning tokens (the figure that used to add a third line to the output card).
_COUNTERS = _summary(
    "today",
    totals={
        "requests": 233445, "input_tokens": 41_564_000_000,
        "output_tokens": 2_323_700_000, "cache_read_tokens": 981_200_000,
        "cache_creation_tokens": 0, "reasoning_output_tokens": 120_000_000,
        "input_cost": 1234.5, "output_cost": 321.0, "cache_read_cost": 88.2,
    },
    prev_totals={
        "requests": 200000, "input_tokens": 40_000_000_000,
        "output_tokens": 2_000_000_000, "cache_read_tokens": 900_000_000,
        "cache_creation_tokens": 0,
    },
)

_FMT_TOK_CASES = [
    (0, "—"), (999, "999"), (1_000, "1.0k"), (123_456, "123.5k"),
    (1_000_000, "1.0M"), (999_999_999, "1,000.0M"),
    (2_323_700_000, "2,323.7M"), (41_564_000_000, "41,564.0M"),
]

_CARDS = """() => [...document.querySelectorAll('#cldCounterGrid .cld-counter')].map(c => {
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


def _the_usage_cards_read_as_one_aligned_set(page, admin_url):
    page.set_viewport_size(PHONE)
    page.route("**/api/code/usage/summary*", lambda route: fulfil_json(route, _COUNTERS))
    _open_code_tab(page, admin_url)
    _await_first_summary(page)
    page.wait_for_function("document.getElementById('cldInputTok').textContent !== '—'", timeout=PANE_TIMEOUT)

    for viewport in (PHONE, DESKTOP):
        for theme in ("light", "dark"):
            page.set_viewport_size(viewport)
            page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
            cards = page.evaluate(_CARDS)
            where = f"{viewport['width']}px {theme}"
            assert len(cards) == 4, where
            heights = [c["height"] for c in cards]
            assert max(heights) - min(heights) <= 1, f"card heights differ ({where}): {heights}"
            # Side by side (desktop) the cards sit at different x, so the right
            # edge is measured from each card's own edge.
            insets = [c["valueInset"] for c in cards]
            assert max(insets) - min(insets) <= 1, f"number right edges differ ({where}): {insets}"
            for c in cards:
                assert "tabular-nums" in c["numerals"], c["numerals"]
                assert c["deltaShown"], "every stub card carries a delta"
                if viewport is PHONE:  # stacked cards: cost and delta share one row
                    assert abs(c["costTop"] - c["deltaTop"]) <= 2, (
                        f"cost and delta are not on one row: {c['costTop']} vs {c['deltaTop']}")
                assert "reasoning" not in c["text"].lower(), c["text"]
            assert page.locator("#cldOutputReasoning").count() == 0

    page.set_viewport_size(PHONE)  # stacked: no two counters share a row
    got = page.evaluate(SIDE_BY_SIDE, "#cldCounterGrid .cld-counter")
    assert got["count"] == 4, got
    assert got["sideBySide"] == 0, f"Code counters share a row on a phone: {got}"

    page.set_viewport_size(DESKTOP)
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", "light")
    assert page.inner_text("#cldInputTok") == "41,564.0M"
    assert page.inner_text("#cldOutputTok") == "2,323.7M"
    assert page.inner_text("#cldCacheRead") == "981.2M"
    # The total cost leads the figures: input + output + cache reads. (fmtCost
    # groups with the browser locale, which may print no separator.)
    assert page.inner_text("#cldTotalCost").replace(",", "").replace(".", "") == "≈ $1644"

    # The requests card has no cost, so its cost line is empty; the line keeps its
    # height with a zero-width space, never a visible glyph.
    content = page.evaluate(
        "() => getComputedStyle(document.querySelector('#cldCounterGrid .cld-counter-cost:empty'), '::before').content")
    assert content == '"​"', content

    # ``fmtTok`` is shared by the Hub, Models and OTel views, so its contract is
    # checked on the function itself, not only through the Code tab.
    for n, expected in _FMT_TOK_CASES:
        assert page.evaluate("n => import('/admin/static/api.js').then(m => m.fmtTok(n))", n) == expected, n


def _slot(n):
    return {"input_tokens": n * 1000, "output_tokens": n * 100, "cache_read_tokens": n * 10, "requests": n}


# Opus leads Anthropic (12 req), then Sonnet (6), Haiku (3), Fable (1): four
# opacity steps plus the floor check below. GPT-5.5 is OpenAI's only model.
# Fable has nothing on Oct 11, so that bucket's tooltip must leave it out.
_SERIES = [
    {"label": "Oct 10", "models": {"Opus": _slot(4), "Sonnet": _slot(2), "Haiku": _slot(1),
                                   "Fable": _slot(1), "GPT-5.5": _slot(5)}},
    {"label": "Oct 11", "models": {"Opus": _slot(5), "Sonnet": _slot(3), "Haiku": _slot(2),
                                   "GPT-5.5": _slot(1), "unknown": _slot(1)}},
    {"label": "Oct 12", "models": {"Opus": _slot(3), "Sonnet": _slot(1)}},
]
_TREND = _summary(
    "month",
    totals={"requests": 30, "input_tokens": 30000, "output_tokens": 3000, "cache_read_tokens": 300,
            "cache_creation_tokens": 0, "input_cost": 1.0, "output_cost": 0.5, "cache_read_cost": 0.1},
    prev_totals={"requests": 30, "input_tokens": 30000, "output_tokens": 3000, "cache_read_tokens": 300,
                 "cache_creation_tokens": 0},
    time_series=_SERIES,
)

_CHART = """() => {
    const c = Chart.getChart(document.getElementById('cldChart'));
    return {
        type: c.config.type,
        stacked: [c.options.scales.x.stacked, c.options.scales.y.stacked],
        labels: c.data.labels,
        sets: c.data.datasets.map(d => ({label: d.label, bg: d.backgroundColor, data: d.data})),
    };
}"""

_ORANGE, _GREEN, _GREY = "219,109,40", "45,164,78", "139,148,158"


def _the_trend_is_stacked_vendor_bars(page, admin_url, mark):
    page.route("**/api/code/usage/summary*", lambda route: fulfil_json(route, _TREND))
    page.set_viewport_size(PHONE)
    _open_code_tab(page, admin_url)
    page.wait_for_selector("#cldChart", state="visible", timeout=PANE_TIMEOUT)
    page.wait_for_function("!!Chart.getChart(document.getElementById('cldChart'))", timeout=PANE_TIMEOUT)

    page.click("#cldCounterGrid button[data-metric='requests']")
    got = page.evaluate(_CHART)
    assert got["type"] == "bar" and got["stacked"] == [True, True], got
    assert got["labels"] == ["Oct 10", "Oct 11", "Oct 12"]
    # Vendor by vendor, most-used first: the solid segment sits at the base.
    assert [(s["label"], s["bg"]) for s in got["sets"]] == [
        ("Opus", f"rgba({_ORANGE},1)"),
        ("Sonnet", f"rgba({_ORANGE},0.75)"),
        ("Haiku", f"rgba({_ORANGE},0.5)"),
        ("Fable", f"rgba({_ORANGE},0.25)"),
        ("GPT-5.5", f"rgba({_GREEN},1)"),
        ("Other", f"rgba({_GREY},1)"),
    ], got["sets"]
    assert got["sets"][0]["data"] == [4, 5, 3]
    mark("code-usage.trend")

    # Tooltip on Oct 11: Fable (zero that day) is left out, the total closes it.
    tip = page.evaluate("""() => {
        const c = Chart.getChart(document.getElementById('cldChart'));
        const active = c.data.datasets.map((d, i) => ({datasetIndex: i, index: 1}));
        c.tooltip.setActiveElements(active, {x: 0, y: 0});
        c.update('none');
        return {body: c.tooltip.body.map(b => b.lines.join('')), footer: c.tooltip.footer};
    }""")
    assert tip["body"] == [" Opus: 5", " Sonnet: 3", " Haiku: 2", " GPT-5.5: 1", " Other: 1"], tip
    assert tip["footer"] == ["Total: 12"], tip

    # Every metric draws the same stacked bars; tokens read through fmtTok.
    for metric in ("input_tokens", "output_tokens", "cache_read_tokens"):
        page.click(f"#cldCounterGrid button[data-metric='{metric}']")
        got = page.evaluate(_CHART)
        assert got["type"] == "bar" and got["stacked"] == [True, True], (metric, got)
        assert len(got["sets"]) == 6, (metric, got["sets"])

    got = page.evaluate("""() => import('/admin/static/code_usage.js').then(m => ({
        vendors: ['Opus', 'Sonnet', 'Haiku', 'Fable', 'claude-x', 'GPT-5.5', 'GPT-5.5 Pro',
                  'gpt-5-codex', 'o3', 'gemini-3-pro', 'mai-ds-r1', 'copilot-chat',
                  'unknown', 'llama-4'].map(m.modelVendor),
        series: m.trendSeries([
            {models: {a1: {requests: 1}, gpt: {requests: 0}}},
            {models: {'gpt-a': {requests: 9}, 'gpt-b': {requests: 8}, 'gpt-c': {requests: 7},
                      'gpt-d': {requests: 6}, 'gpt-e': {requests: 5}, 'gemini-x': {requests: 2},
                      'Opus': {requests: 1}}},
        ], 'requests').map(s => [s.key, s.vendor, s.opacity, s.data]),
    }))""")
    assert got["vendors"] == [
        "anthropic", "anthropic", "anthropic", "anthropic", "anthropic",
        "openai", "openai", "openai", "openai", "google", "microsoft", "microsoft",
        "other", "other",
    ]
    # Zero-total models drop out; each vendor ranks on its own, floor 25 %.
    assert got["series"] == [
        ["Opus", "anthropic", 1, [0, 1]],
        ["gpt-a", "openai", 1, [0, 9]],
        ["gpt-b", "openai", 0.75, [0, 8]],
        ["gpt-c", "openai", 0.5, [0, 7]],
        ["gpt-d", "openai", 0.25, [0, 6]],
        ["gpt-e", "openai", 0.25, [0, 5]],
        ["gemini-x", "google", 1, [0, 2]],
        ["a1", "other", 1, [1, 0]],
    ]


def _empty_states_wait_for_the_summary_and_the_first_screen_fits(page, admin_url, browser_name, mark):
    held = []
    released = []

    def _hold(route):
        # Hold requests until the test has looked at the pre-load state, then
        # let everything (including the 30 s poll) straight through.
        if released:
            route.continue_()
        else:
            held.append(route)

    page.route("**/admin/api/code/usage/summary*", _hold)
    page.set_viewport_size(PHONE)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#tabCodeUsage", state="visible", timeout=5000)
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)
    # The other panes must be hidden: wait for state, don't race the DOM.
    for pane in ("#paneHub", "#paneModels", "#panePlayground"):
        page.wait_for_selector(pane, state="hidden", timeout=PANE_TIMEOUT)
    # All four counter elements are present (content may be "—" or a value).
    for counter in ("#cldRequests", "#cldInputTok", "#cldOutputTok", "#cldCacheRead"):
        assert page.locator(counter).count() == 1, counter

    deadline = time.monotonic() + 10
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert held, "the Code tab never asked for its usage summary"

    # Before the summary lands: one honest "reading" line, no header-only tables,
    # no "No sessions found" claim.
    page.wait_for_selector("#cldPending", state="visible", timeout=5000)
    for card in ("#cldProjectCard", "#cldChartsCard"):
        assert page.locator(card).is_hidden(), f"{card} is showing before the first summary"
    mark("code-usage.loading")

    released.append(True)
    for route in held:
        route.continue_()
    page.wait_for_selector("#cldPending", state="hidden", timeout=PANE_TIMEOUT)

    # After it lands (the e2e hub has no usage history): every empty state is
    # visible where it lives (the card, or the sheet its row opens) and names the
    # control that changes it (the period switch or agent filter above) or
    # carries its own action button.
    for opener, empty, name in (
        (None, "#cldProjectEmpty", "code-usage.project-empty"),
        ("#cldModelOpenBtn", "#cldModelEmpty", "code-usage.model-sheet-empty"),
        ("#cldSessionsOpenBtn", "#cldSessionsEmpty", "code-usage.sessions-sheet-empty"),
    ):
        if opener:
            page.click(opener)
        page.wait_for_selector(empty, state="visible", timeout=5000)
        text = page.locator(f"{empty} .empty-state-message").inner_text().lower()
        mark(name)
        if opener:
            page.keyboard.press("Escape")
            page.wait_for_selector(empty, state="hidden", timeout=3000)
        has_action = page.locator(f"{empty} .empty-state-action").count() > 0
        assert has_action or "above" in text, f"{empty} names no control: {text!r}"
        assert "today" not in text, f"{empty} still says 'today' for every period: {text!r}"

    # The first screen: the period switch, the four counters and the total cost
    # fit without scrolling, with no paragraph above the numbers, on a phone and
    # on a PC window.
    _await_first_summary(page)
    for viewport in (PHONE, {"width": 1280, "height": 800}):
        page.set_viewport_size(viewport)
        got = page.evaluate(
            """() => {
                const grid = document.querySelector('#cldCounterGrid').getBoundingClientRect();
                return {
                    bottom: Math.max(...['#cldPeriodSeg', '#cldTotalCost', '#cldCounterGrid'].map(
                        s => document.querySelector(s).getBoundingClientRect().bottom)),
                    paragraphs: [...document.querySelectorAll('#cldTotalsCard p')].filter(
                        p => p.offsetParent && p.getBoundingClientRect().top < grid.top).length,
                };
            }"""
        )
        assert got["bottom"] <= viewport["height"], f"first screen overflows at {viewport}: {got}"
        assert got["paragraphs"] == 0, got
    page.set_viewport_size(PHONE)
    snapshot(page, "code-usage-390x844", browser_name)
    mark("code-usage.tab")


def _a_failed_summary_is_an_error_not_a_zero_day(page, admin_url, mark):
    """A failed summary load is visibly distinct from an idle day: first the 503
    the router returns on a failed build, then a genuinely zero-usage 200."""
    state = {"fail": True}
    zero = _summary("week", totals={
        "requests": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "input_cost": 0, "output_cost": 0, "cache_read_cost": 0,
    })

    def handler(route):
        if state["fail"]:
            route.fulfill(
                status=503, content_type="application/json",
                body='{"period":"today","vendor":"all","error":"transcript store unreadable",'
                     '"detail":"Could not build the code-usage summary: transcript store unreadable"}',
            )
        else:
            fulfil_json(route, zero)

    page.set_viewport_size({"width": 800, "height": 900})
    page.route("**/api/code/usage/summary*", handler)
    _open_code_tab(page, admin_url)

    # An error state, and no zero-shaped numbers anywhere.
    page.wait_for_selector("#cldError", state="visible", timeout=PANE_TIMEOUT)
    assert "transcript store unreadable" in page.inner_text("#cldErrorMsg")
    for sel in ("#cldRequests", "#cldInputTok", "#cldOutputTok", "#cldCacheRead"):
        assert page.inner_text(sel) == "—", f"{sel} is not the unknown dash"
    assert page.inner_text("#cldSessionsBadge") == "—"  # unknown, not known to be none
    page.wait_for_selector("#cldChartsCard", state="hidden", timeout=PANE_TIMEOUT)
    assert "Usage not loaded" in page.inner_text("#codeHeadLine")
    mark("code-usage.error")

    # Recovery into a real zero-usage period: zeroes, no error. The error state is
    # driven by what the server reported, never inferred from the totals being empty.
    state["fail"] = False
    page.evaluate("document.querySelector('#cldPeriodSeg button[data-period=\"week\"]').click()")
    page.wait_for_selector("#cldError", state="hidden", timeout=PANE_TIMEOUT)
    page.wait_for_function("document.getElementById('cldRequests').textContent === '0'", timeout=PANE_TIMEOUT)
    assert page.inner_text("#cldSessionsBadge") == "0 sessions"
    assert "Usage not loaded" not in page.inner_text("#codeHeadLine")
    mark("code-usage.idle-period")


def test_code_usage_renders_the_summary(browser, admin_url, browser_name):
    with fresh_page(browser) as page:
        _the_usage_cards_read_as_one_aligned_set(page, admin_url)
    with fresh_page(browser) as page:
        _the_trend_is_stacked_vendor_bars(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser, console_errors=True) as page:
        _empty_states_wait_for_the_summary_and_the_first_screen_fits(
            page, admin_url, browser_name, lambda name: surface(page, name))
    with fresh_page(browser) as page:  # the browser logs the stubbed 503 itself
        _a_failed_summary_is_an_error_not_a_zero_day(page, admin_url, lambda name: surface(page, name))


# --------------------------------------------------------------------------- #
# The controls
# --------------------------------------------------------------------------- #

_COUNT_POLL_INTERVALS = """
(() => {
  // Live 30 s intervals (the Code usage poll cadence), tracked by handle so a
  // clearInterval on the wrong handle leaves the orphan counted.
  const live = new Set();
  const set = window.setInterval, clear = window.clearInterval;
  window.setInterval = function (fn, ms, ...rest) {
    const id = set.call(window, fn, ms, ...rest);
    if (ms === 30000) live.add(id);
    return id;
  };
  window.clearInterval = function (id) { live.delete(id); return clear.call(window, id); };
  window.__livePollIntervals = () => live.size;
})();
"""

_AGENTS = _summary(
    "today",
    totals={"requests": 5, "input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0,
            "cache_creation_tokens": 0, "input_cost": 1.0, "output_cost": 0.5, "cache_read_cost": 0},
    by_vendor=[
        {"vendor": "claude", "requests": 3, "input_tokens": 60, "output_tokens": 30,
         "input_cost": 0.6, "output_cost": 0.3, "cache_read_cost": 0},
        {"vendor": "agy", "requests": 2, "input_tokens": 40, "output_tokens": 20,
         "input_cost": 0.4, "output_cost": 0.2, "cache_read_cost": 0},
    ],
    agentsview={"enabled": True, "reachable": False, "vendors": ["agy"],
                "last_refresh": "2026-10-11T08:30:00+00:00"},
)

_SUB_COLUMNS = ["Date", "Project", "Model", "Source", "In", "Out", "Cache reads", "Cache writes", "≈ $"]


def _sub_body(period):
    if period == "all":
        return {"period": period, "rows": [], "totals": {}}
    return {"period": period, "totals": {"input": 1200, "output": 300, "cost_usd": 0.42}, "rows": [
        {"date": "2026-10-10", "project": "demo-project", "model": "demo-model", "query_source": "sub-agent",
         "input": 1200, "output": 300, "cache_read": 0, "cache_creation": 0, "cost_usd": 0.42},
    ]}


def _wait_for(page, cond):
    for _ in range(80):
        if cond():
            return True
        page.wait_for_timeout(50)
    return cond()


def _retapping_the_active_tab_leaves_one_poll_interval(page, admin_url):
    """The nav re-fires onChange on a tap of the already-active tab, and each
    repeat used to start another 30 s interval whose handle was then lost, so
    leaving the tab could not stop it."""
    page.add_init_script(_COUNT_POLL_INTERVALS)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#tabCodeUsage", state="visible", timeout=5000)
    baseline = page.evaluate("window.__livePollIntervals()")

    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)
    _await_first_summary(page)
    assert page.evaluate("window.__livePollIntervals()") == baseline + 1

    for _ in range(3):
        page.click("#tabCodeUsage")
    assert page.evaluate("window.__livePollIntervals()") == baseline + 1

    page.click("#tabHub")
    page.wait_for_selector("#paneCodeUsage", state="hidden", timeout=PANE_TIMEOUT)
    assert page.evaluate("window.__livePollIntervals()") == baseline


def _the_agent_sheet_and_the_period_switch_choose_the_source(page, admin_url):
    """The agent sheet lists every agent (a runtime AgentsView extra with its
    freshness too) and the Sub-agents (OTel) source."""
    summaries, subs = [], []

    def summary(route):
        q = parse_qs(urlparse(route.request.url).query)
        summaries.append((q["period"][0], q["vendor"][0]))
        fulfil_json(route, dict(_AGENTS, period=q["period"][0], vendor=q["vendor"][0]))

    def sub_usage(route):
        period = parse_qs(urlparse(route.request.url).query)["period"][0]
        subs.append(period)
        fulfil_json(route, _sub_body(period))

    page.route("**/api/code/usage/summary*", summary)
    page.route("**/api/telemetry/claude-code/usage*", sub_usage)
    page.set_viewport_size({"width": 800, "height": 900})
    _open_code_tab(page, admin_url)
    _await_first_summary(page)
    assert page.inner_text("#cldAgentLabel") == "All agents"
    # The offline AgentsView behind the extra is the header's exception.
    assert "AgentsView offline" in page.inner_text("#codeHeadLine")
    page.click("#cldAgentBtn")
    sheet = page.locator("#cldAgentSheet[open]")
    sheet.wait_for(timeout=3000)
    titles = sheet.locator(".cld-agent-row .action-row-title").all_inner_texts()
    assert titles == ["All agents", "Claude", "Codex", "Copilot", "AGY", "Sub-agents (telemetry)"], titles
    agy = sheet.locator('.cld-agent-row[data-vendor="agy"]').inner_text()
    assert "AgentsView" in agy and "offline" in agy and "2 req" in agy, agy

    sheet.locator('.cld-agent-row[data-vendor="codex"] .action-row-main').click()
    page.wait_for_selector("#cldAgentSheet", state="hidden", timeout=3000)
    assert page.inner_text("#cldAgentLabel") == "Codex"
    assert _wait_for(page, lambda: ("today", "codex") in summaries), summaries
    page.wait_for_selector("#cldVendorCard", state="hidden", timeout=PANE_TIMEOUT)  # the per-agent table is an All view

    # Sub-agents (OTel): the pane's table for the one period switch above.
    page.click("#cldAgentBtn")
    page.locator('#cldAgentSheet .cld-agent-row[data-vendor="subagents"] .action-row-main').click()
    page.wait_for_selector("#cldSubagents", state="visible", timeout=PANE_TIMEOUT)
    assert page.locator("#cldFigures").is_hidden()
    assert page.locator("#cldSubTable thead th").all_inner_texts() == _SUB_COLUMNS
    page.wait_for_function(
        "document.getElementById('cldSubSummary').textContent.includes('$0.42')", timeout=PANE_TIMEOUT)
    assert "never added into All" in page.inner_text("#cldSubagents")
    assert "today" in subs
    for period in ("week", "month", "all"):
        page.evaluate("p => document.querySelector('#cldPeriodSeg button[data-period=\"' + p + '\"]').click()", period)
        assert _wait_for(page, lambda: bool(subs) and subs[-1] == period), subs
        active = page.evaluate("document.querySelector('#cldPeriodSeg [aria-pressed=\"true\"]')?.dataset.period")
        assert active == period, f"expected {period!r}, got {active!r}"
    page.wait_for_selector("#cldSubEmpty", state="visible", timeout=PANE_TIMEOUT)
    # The separate store never reaches the summary route.
    assert not [v for _, v in summaries if v == "subagents"], summaries


# One distinct request count per period, so the cards prove which choice's data
# they are showing.
_REQUESTS = {"today": 1, "week": 7, "month": 30, "all": 99}


def _loading_body(period: str, vendor: str = "all") -> dict:
    return _summary(period, vendor, totals={
        "requests": _REQUESTS[period], "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 100, "cache_creation_tokens": 0, "input_cost": 1,
        "output_cost": 1, "cache_read_cost": 0})


def _query(route):
    q = parse_qs(urlparse(route.request.url).query)
    return q["period"][0], q["vendor"][0]


class _HeldSummary:
    """Stubs the summary route; once ``hold`` is set it parks every request."""

    def __init__(self, page):
        self.hold = False
        self.held: list = []
        self.seen: list = []
        self.failed: list = []
        page.route("**/api/code/usage/summary*", self._handle)
        page.on("requestfailed", lambda r: (
            self.failed.append(r.url) if "/code/usage/summary" in r.url else None))

    def _handle(self, route):
        self.seen.append(_query(route))
        if self.hold:
            self.held.append(route)
            return
        period, vendor = _query(route)
        fulfil_json(route, _loading_body(period, vendor))

    def answer_last(self):
        route = self.held.pop()
        period, vendor = _query(route)
        fulfil_json(route, _loading_body(period, vendor))

    def forget(self):
        """Start a scenario clean: nothing parked, nothing aborted."""
        self.held.clear()
        self.failed.clear()


def _click(page, selector):
    page.evaluate(f"document.querySelector('{selector}').click()")


def _wait_held(page, summary, n):
    page.wait_for_function("() => true")  # let queued events run
    for _ in range(100):
        if len(summary.held) >= n:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"expected {n} held request(s), saw {len(summary.held)}")


def _wait_requests(page, value):
    page.wait_for_function(
        f"document.getElementById('cldRequests').textContent === '{value}'", timeout=PANE_TIMEOUT)


def _a_switch_says_it_is_loading_until_the_data_lands(page, admin_url):
    summary = _HeldSummary(page)
    page.set_viewport_size({"width": 800, "height": 900})
    _open_code_tab(page, admin_url)
    _wait_requests(page, 1)
    summary.hold = True
    toast = page.locator("#toast")
    assert toast.is_hidden()

    # A switch shows the frosted "Loading usage…" toast until the data lands.
    summary.forget()
    _click(page, '#cldPeriodSeg button[data-period="week"]')
    toast.wait_for(state="visible", timeout=2000)
    assert toast.inner_text() == "Loading usage…"
    _wait_held(page, summary, 1)
    page.wait_for_timeout(2600)  # still loading: the toast does not auto-hide on its 2.2 s timer
    assert toast.is_visible()
    summary.answer_last()
    toast.wait_for(state="hidden", timeout=5000)
    _wait_requests(page, 7)

    # Rapid clicks fire one request, for the final choice, and the cards show that
    # choice's data.
    summary.forget()
    before = len(summary.seen)
    for period in ("week", "month", "all"):
        _click(page, f'#cldPeriodSeg button[data-period="{period}"]')
    _wait_held(page, summary, 1)
    page.wait_for_timeout(600)
    fired = summary.seen[before:]
    assert fired == [("all", "all")], f"expected one request for the final choice, got {fired}"
    summary.answer_last()
    toast.wait_for(state="hidden", timeout=5000)
    _wait_requests(page, 99)
    assert page.evaluate("document.querySelector('#cldPeriodSeg [aria-pressed=\"true\"]').dataset.period") == "all"

    # A click after the request is out aborts the stale one.
    summary.forget()
    _click(page, '#cldPeriodSeg button[data-period="week"]')
    _wait_held(page, summary, 1)  # the week request is on the wire
    _click(page, '#cldPeriodSeg button[data-period="month"]')
    _wait_held(page, summary, 2)
    for _ in range(40):
        if summary.failed:
            break
        page.wait_for_timeout(50)
    assert summary.failed, "the stale week request was not aborted"
    summary.answer_last()  # the month request
    toast.wait_for(state="hidden", timeout=5000)
    _wait_requests(page, 30)

    # The agent is chosen in its sheet; the toast is the frosted neutral glass.
    summary.forget()
    page.click("#cldAgentBtn")
    page.click('#cldAgentSheet .cld-agent-row[data-vendor="codex"] .action-row-main')
    toast.wait_for(state="visible", timeout=2000)
    style = toast.evaluate(
        "e => { const s = getComputedStyle(e); return { f: s.backdropFilter || s.webkitBackdropFilter, w: s.fontWeight }; }")
    assert "blur" in style["f"], style
    assert int(style["w"]) >= 700, style
    _wait_held(page, summary, 1)
    summary.answer_last()
    toast.wait_for(state="hidden", timeout=5000)

    # A failed fetch shows the error toast and keeps the error banner.
    summary.forget()
    _click(page, '#cldPeriodSeg button[data-period="week"]')
    _wait_held(page, summary, 1)
    summary.held.pop().fulfill(
        status=503, content_type="application/json",
        body='{"period":"week","vendor":"codex","error":"transcript store unreadable",'
             '"detail":"Could not build the code-usage summary: transcript store unreadable"}',
    )
    page.wait_for_selector("#cldError", state="visible", timeout=PANE_TIMEOUT)
    toast.wait_for(state="visible", timeout=2000)
    assert "error" in (toast.get_attribute("class") or "")
    assert "Loading usage" not in toast.inner_text()
    assert "transcript store unreadable" in page.inner_text("#cldErrorMsg")


def _the_background_poll_shows_no_toast(page, admin_url):
    page.clock.install()
    summary = _HeldSummary(page)
    page.set_viewport_size({"width": 800, "height": 900})
    _open_code_tab(page, admin_url)
    _wait_requests(page, 1)
    summary.hold = True
    before = len(summary.seen)

    page.clock.fast_forward(31_000)  # past the 30 s poll interval
    _wait_held(page, summary, 1)
    assert len(summary.seen) > before, "the poll did not fetch"
    assert page.locator("#toast").is_hidden()
    summary.answer_last()
    page.wait_for_timeout(300)
    assert page.locator("#toast").is_hidden()


def test_code_usage_switching(browser, admin_url):
    with fresh_page(browser, console_errors=True) as page:
        _retapping_the_active_tab_leaves_one_poll_interval(page, admin_url)
    with fresh_page(browser, console_errors=True) as page:
        _the_agent_sheet_and_the_period_switch_choose_the_source(page, admin_url)
    with fresh_page(browser) as page:  # the browser logs the stubbed 503 itself
        _a_switch_says_it_is_loading_until_the_data_lands(page, admin_url)
    with fresh_page(browser) as page:
        _the_background_poll_shows_no_toast(page, admin_url)
