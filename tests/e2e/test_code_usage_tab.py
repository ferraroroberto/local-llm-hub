"""End-to-end tests for the SPA Claude Code usage tab (issue #20).

Boots the hub with OTel disabled (set in tests/e2e/_isolated_hub.py).

The test hub reads no usage history (see _isolate_usage_data in
tests/e2e/_isolated_hub.py), so every run renders the empty-state path of
/admin/api/code/usage/summary.

Checks:
  - Tab button is visible and clickable.
  - Switching to the tab hides the other panes.
  - The four counter elements are rendered (may show "—" when empty).
  - The period switch and the agent sheet switch the choice (#739): every
    agent including a runtime AgentsView extra with its freshness, and the
    Sub-agents (OTel) source driven by the one period switch, never summed
    into All.
  - A failed summary renders as an error, not a zero-usage day (#580).
  - The first screen holds the period switch, the counters and the total
    cost on a phone and on a PC window; a phone screenshot is saved.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("admin_url")

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
PHONE_VIEWPORT = {"width": 390, "height": 844}
# Pane-switch DOM/CSS transition can overrun a tight budget under runner
# contention (issue #177) — give it the same headroom as the button wait.
PANE_TIMEOUT = 10000


@pytest.fixture(scope="session", autouse=True)
def _usage_data_is_hermetic(admin_url):
    """Fail fast if the test hub is reading this machine's usage history.

    ``tests/e2e/_isolated_hub.py`` points every Code Usage source at an empty
    path. Left pointing at the host's real session history, the summary
    endpoint cold-scans it (about 40 s on a ~300k-record history, over half
    the suite's wall time) and the tab renders whoever's data is on the box.
    The all-time, all-vendor summary is the widest view, so zero requests
    there means no source leaked in.
    """
    base = admin_url.rstrip("/") + "/api/code/usage/summary"
    r = httpx.get(base, params={"period": "all", "vendor": "all"}, timeout=30.0)
    assert r.status_code == 200, f"usage summary failed: {r.text}"
    assert r.json()["totals"]["requests"] == 0, (
        "the e2e hub read real usage history; see _isolate_usage_data in "
        "tests/e2e/_isolated_hub.py"
    )


@pytest.fixture(autouse=True)
def _no_console_errors(page, request):
    # A test that deliberately stubs a failing response opts out by marker —
    # the browser logs the non-2xx itself, and that noise is the point of the
    # test rather than a regression (#580).
    if request.node.get_closest_marker("expect_console_errors"):
        yield
        return
    errs = []

    def _on_console(msg):
        if msg.type != "error":
            return
        text = msg.text
        # PWA icons are optional placeholders; ignore their 404s.
        if "icon-180" in text or "icon-512" in text:
            return
        errs.append(text)

    page.on("console", _on_console)
    yield
    if errs:
        raise AssertionError("console errors: " + " | ".join(errs))



def _await_first_summary(page):
    """Block until the tab's first summary poll has been fetched *and* drawn.

    ``#cldFreshness`` starts empty and only reads ``updated …`` once ``render``
    has run, so this is the state the old fixed 2 s sleep was standing in for.
    """
    page.wait_for_function(
        "(document.getElementById('cldFreshness')?.textContent || '').startsWith('updated')",
        timeout=PANE_TIMEOUT,
    )


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


def test_retapping_the_active_tab_leaves_one_poll_interval(page, admin_url):
    """#787: the nav re-fires onChange on a tap of the already-active tab, and
    each repeat used to start another 30 s interval whose handle was then lost,
    so leaving the tab could not stop it."""
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


def test_code_usage_tab_loads(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    # Tab button must be present and visible.
    page.wait_for_selector("#tabCodeUsage", state="visible", timeout=5000)
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)
    # Other panes must be hidden — wait for state, don't race the DOM.
    page.wait_for_selector("#paneHub", state="hidden", timeout=PANE_TIMEOUT)
    page.wait_for_selector("#paneModels", state="hidden", timeout=PANE_TIMEOUT)
    page.wait_for_selector("#panePlayground", state="hidden", timeout=PANE_TIMEOUT)
    # All four counter elements must be present (content may be "—" or a value).
    assert page.locator("#cldRequests").count() == 1
    assert page.locator("#cldInputTok").count() == 1
    assert page.locator("#cldOutputTok").count() == 1
    assert page.locator("#cldCacheRead").count() == 1


def test_period_toggle_changes_counters(page, admin_url):
    """Clicking 'Week' toggles the active button; counters update.

    The period toggle sits inside the first card — we fire the click via JS
    to avoid any viewport-clipping issues in the headless runner.
    """
    page.set_viewport_size({"width": 800, "height": 900})
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)
    _await_first_summary(page)
    # Fire the click via JS so viewport clipping doesn't block us.
    page.evaluate(
        "document.querySelector('#cldPeriodSeg button[data-period=\"week\"]').click()"
    )
    active_period = page.evaluate(
        "document.querySelector('#cldPeriodSeg [aria-pressed=\"true\"]')?.dataset.period"
    )
    assert active_period == "week", f"expected 'week', got {active_period!r}"


_AV_BODY = {
    "period": "today", "vendor": "all",
    "totals": {"requests": 5, "input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0,
               "cache_creation_tokens": 0, "input_cost": 1.0, "output_cost": 0.5, "cache_read_cost": 0},
    "daily": [], "by_model": [], "by_project": [], "by_vendor": [
        {"vendor": "claude", "requests": 3, "input_tokens": 60, "output_tokens": 30,
         "input_cost": 0.6, "output_cost": 0.3, "cache_read_cost": 0},
        {"vendor": "agy", "requests": 2, "input_tokens": 40, "output_tokens": 20,
         "input_cost": 0.4, "output_cost": 0.2, "cache_read_cost": 0},
    ],
    "recent_sessions": [], "time_series": [],
    "agentsview": {"enabled": True, "reachable": False, "vendors": ["agy"],
                   "last_refresh": "2026-10-11T08:30:00+00:00"},
}

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


def test_vendor_toggle_changes_selector(page, admin_url):
    """The agent sheet lists every agent (a runtime AgentsView extra with its
    freshness too) and the Sub-agents (OTel) source (#739, issues #71, #280)."""
    import json as _json
    from urllib.parse import parse_qs, urlparse

    summaries, subs = [], []

    def summary(route):
        q = parse_qs(urlparse(route.request.url).query)
        summaries.append((q["period"][0], q["vendor"][0]))
        body = dict(_AV_BODY, period=q["period"][0], vendor=q["vendor"][0])
        route.fulfill(status=200, content_type="application/json", body=_json.dumps(body))

    def sub_usage(route):
        period = parse_qs(urlparse(route.request.url).query)["period"][0]
        subs.append(period)
        route.fulfill(status=200, content_type="application/json", body=_json.dumps(_sub_body(period)))

    page.route("**/api/code/usage/summary*", summary)
    page.route("**/api/telemetry/claude-code/usage*", sub_usage)
    page.set_viewport_size({"width": 800, "height": 900})
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)
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
    # The per-agent table is an All view.
    page.wait_for_selector("#cldVendorCard", state="hidden", timeout=PANE_TIMEOUT)

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
    page.wait_for_selector("#cldSubEmpty", state="visible", timeout=PANE_TIMEOUT)
    # The separate store never reaches the summary route.
    assert not [v for _, v in summaries if v == "subagents"], summaries


def test_code_usage_tab_phone_screenshot(page, admin_url, browser_name):
    page.set_viewport_size(PHONE_VIEWPORT)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)
    # Wait for the first poll to complete (counters fill in). The counters
    # start as "—", so their text is no signal; the freshness stamp is.
    _await_first_summary(page)
    # The first screen (#739): the period switch, the four counters and the
    # total cost fit without scrolling, with no paragraph above the numbers,
    # on a phone and on a PC window.
    for viewport in (PHONE_VIEWPORT, {"width": 1280, "height": 800}):
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
    page.set_viewport_size(PHONE_VIEWPORT)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOT_DIR / f"code-usage-390x844-{browser_name}.png"
    page.screenshot(path=str(out), full_page=True)
    assert out.exists() and out.stat().st_size > 0


# The shape render() needs for a genuinely idle period: a *successful* summary
# whose numbers happen to be zero. Kept next to the failure case below because
# the whole point of #580 is that these two must not look alike.
_ZERO_USAGE_BODY = {
    "period": "week", "vendor": "all",
    "totals": {
        "requests": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "input_cost": 0, "output_cost": 0, "cache_read_cost": 0,
    },
    "daily": [], "by_model": [], "by_project": [], "by_vendor": [],
    "recent_sessions": [], "time_series": [],
    "agentsview": {"enabled": False, "reachable": False, "vendors": []},
}


@pytest.mark.expect_console_errors
def test_failed_summary_renders_as_error_not_zero_usage(page, admin_url):
    """#580: a failed summary load must be visibly distinct from an idle day.

    Drives the real client against a stubbed route so both halves are proven
    in one pass — first the 503 the router now returns on a failed build, then
    a genuinely zero-usage 200 — asserting the UI tells them apart.
    """
    page.set_viewport_size({"width": 800, "height": 900})

    state = {"fail": True}

    def handler(route):
        if state["fail"]:
            route.fulfill(
                status=503, content_type="application/json",
                body='{"period":"today","vendor":"all",'
                     '"error":"transcript store unreadable",'
                     '"detail":"Could not build the code-usage summary: '
                     'transcript store unreadable"}',
            )
        else:
            import json as _json
            route.fulfill(
                status=200, content_type="application/json",
                body=_json.dumps(_ZERO_USAGE_BODY),
            )

    page.route("**/api/code/usage/summary*", handler)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)

    # --- failure: an error state, and no zero-shaped numbers anywhere ---
    page.wait_for_selector("#cldError", state="visible", timeout=PANE_TIMEOUT)
    assert "transcript store unreadable" in page.inner_text("#cldErrorMsg")
    for sel in ("#cldRequests", "#cldInputTok", "#cldOutputTok", "#cldCacheRead"):
        assert page.inner_text(sel) == "—", f"{sel} is not the unknown dash"
    # The session count is unknown, not known to be none.
    assert page.inner_text("#cldSessionsBadge") == "—"
    page.wait_for_selector("#cldChartsCard", state="hidden", timeout=PANE_TIMEOUT)
    # The header line names the failure too (#739).
    assert "Usage not loaded" in page.inner_text("#codeHeadLine")

    # --- recovery into a real zero-usage period: zeroes, no error ---
    state["fail"] = False
    page.evaluate(
        "document.querySelector('#cldPeriodSeg button[data-period=\"week\"]').click()"
    )
    page.wait_for_selector("#cldError", state="hidden", timeout=PANE_TIMEOUT)
    # A real zero day keeps its honest zero — the error state is driven by what
    # the server reported, never inferred from the totals being empty.
    page.wait_for_function(
        "document.getElementById('cldRequests').textContent === '0'",
        timeout=PANE_TIMEOUT,
    )
    assert page.inner_text("#cldSessionsBadge") == "0 sessions"
    assert "Usage not loaded" not in page.inner_text("#codeHeadLine")
