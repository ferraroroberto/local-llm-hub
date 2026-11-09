"""The Hub landing: what the admin SPA shows on a first visit.

One phone-width visit to ``/admin/`` reads the whole Hub tab: the console stays
clean, the default pane is Hub on its glance, the boot burst leaves out the
live-probe endpoints, the load sparkline paints before the first poll, the
tiles stack under a heading, each empty list names its control, labels are
plain words and every pane header holds only the theme toggle and the gear.

Runs before ``test_traffic.py`` on purpose: the empty-state checks need a hub
that has served no model traffic yet.

Runs under Chromium only; the SPA has no Safari-specific code.
"""

from __future__ import annotations

import re

from tests.e2e._flow import PHONE, SIDE_BY_SIDE, fresh_page, snapshot
from tests.e2e._surface import surface

# Each takes seconds and neither feeds the landing view; they load when their
# card opens (Health & install) or when the Models tab opens.
_NOT_AT_BOOT = ("/admin/api/install/status", "/admin/api/fleet-placement", "/admin/api/roles")

# Whole-word, case-sensitive: "Requests" is fine, "Req" is not.
_JARGON = re.compile(r"\b(JSON|noul|Jev|Req|Err|tok|rd|cr|p50|p95|PCM)\b")
_LABEL_SELECTORS = "th, label, .collapse-title, .card-header h2, .card-header h3"

_PAINT = """els => els.map(el => {
    const s = getComputedStyle(el);
    return [s.backgroundColor, s.borderTopWidth];
})"""


def _boots_clean_into_hub(page, admin_url, requested):
    """The default pane is Hub, the sparkline paints without waiting for the
    first 2.5 s poll, and the boot burst skips the endpoints that probe."""
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#hubSparklines .sparkline", state="attached", timeout=1500)
    page.wait_for_selector("#tabHub", state="visible", timeout=5000)
    page.wait_for_selector("#paneHub", state="visible", timeout=3000)
    page.wait_for_selector("#paneModels", state="hidden", timeout=3000)
    page.wait_for_selector("#panePlayground", state="hidden", timeout=3000)
    page.wait_for_selector("#paneHub .home-head", state="visible", timeout=5000)
    page.wait_for_function(
        "document.getElementById('hubHeadLine').textContent.indexOf('Reading') === -1", timeout=8000)
    page.wait_for_timeout(300)  # gives an accidental eager fetch a chance to fire
    boot = [u for u in requested if any(u.endswith(s) for s in _NOT_AT_BOOT)]
    assert boot == [], f"boot fetched {boot}"


def _opens_on_the_glance(page, mark, browser_name):
    """Numbers first, the window they cover named, no permanent green "up",
    and neither the peer rows nor the Model decisions card on Hub."""
    page.wait_for_function(
        "document.getElementById('hubHeadLine').textContent.indexOf('Reading') === -1"
        " && document.getElementById('hubReqCount').textContent !== '—'",
        timeout=8000,
    )
    first = page.evaluate("() => document.querySelector('#paneHub .hub-col > .card').id")
    assert first == "hubGlance", first
    assert page.locator("#hubReqCount").text_content().strip().isdigit()
    assert "since start" in page.locator("#hubWindow").text_content()
    head = page.locator("#hubHeadLine")
    assert head.locator("[data-tone='success']").count() == 0
    assert head.text_content().strip().lower() != "up"
    assert page.locator("#paneHub #peerRows, #paneHub #rolesCard").count() == 0
    snapshot(page, "hub-390x844", browser_name)
    mark("hub.tab")


def _tiles_stack_under_a_heading(page):
    """The first card opens on a heading, one load sparkline whose series control
    swaps the history it draws, and the glance figures share one row."""
    first_card = page.locator("#paneHub .hub-col > section.card").first
    assert first_card.get_attribute("id") == "hubGlance"
    assert first_card.locator(":scope > .card-header h2").count() == 1, (
        "the first Hub card should open on a heading, not on the PID/uptime line"
    )
    assert page.locator("#hubSparklines .sparkline").count() == 1
    series = page.locator("#hubSeries .segmented-item")
    series.first.wait_for(state="visible", timeout=5000)
    assert series.count() >= 2
    ram = page.locator('#hubSeries .segmented-item[data-series="ram"]')
    ram.click()
    page.wait_for_function(
        "document.querySelector('#hubSparklines .sparkline-label').textContent.startsWith('RAM')",
        timeout=3000,
    )
    assert ram.get_attribute("aria-pressed") == "true"
    got = page.evaluate(SIDE_BY_SIDE, "#hubGlance .hub-figure")
    assert got["count"] == 3, got


def _empty_lists_name_their_control(page, mark):
    """The e2e hub has served no model traffic, so each list is in its empty state."""
    action = page.locator("#liveRequestsEmpty .empty-state-action")
    assert action.count() == 1, "#liveRequestsEmpty has no action"
    assert action.get_attribute("data-open-tab") == "playground"
    page.wait_for_function(
        "(document.querySelector('#countersTable tbody')?.textContent || '').includes('No requests yet')",
        timeout=10000,
    )
    counters = page.locator("#countersTable tbody").text_content() or ""
    assert "No requests yet" in counters, counters
    assert "Playground" in counters, f"counters empty row names no control: {counters!r}"
    errors_action = page.locator("#recentErrorsEmpty .empty-state-action")
    assert errors_action.count() == 1, "recent errors empty state has no action"
    assert errors_action.get_attribute("data-open-tab") == "playground"
    mark("hub.empty-counters")


def _labels_and_headers_are_plain(page):
    """Plain words in every label, the verbs spelled out on the service buttons,
    and every pane header holding exactly the theme toggle and the gear."""
    texts = page.evaluate(
        r"""sel => [...document.querySelectorAll(sel)]
            .map(el => (el.textContent || '').replace(/\s+/g, ' ').trim())
            .filter(Boolean)""",
        _LABEL_SELECTORS,
    )
    assert texts, "no labels found: selector drifted"
    offenders = sorted({t for t in texts if _JARGON.search(t)})
    assert not offenders, f"labels with jargon or abbreviations: {offenders}"

    # The six buttons are shown one at a time by service state; read the words
    # off the DOM rather than the state.
    for service in ("docker", "langfuse", "agentsview"):
        for verb in ("Start", "Stop"):
            btn = page.locator(f"#{service}{verb}Btn")
            assert btn.count() == 1, f"missing #{service}{verb}Btn"
            assert (btn.text_content() or "").strip() == verb, f"{service} {verb} is icon-only"

    heads = page.evaluate(
        """() => [...document.querySelectorAll('.home-head')].map(h =>
            [...h.querySelectorAll('button')].map(b =>
                b.classList.contains('theme-toggle') ? 'theme' : b.classList.contains('home-settings') ? 'gear' : b.className))"""
    )
    assert len(heads) == 5, heads
    assert all(h == ["theme", "gear"] for h in heads), heads

    # The header toggles and dialog closes are .icon-button's: a glyph on nothing at rest.
    toggles = page.locator("#paneHub .home-head .home-toggle")
    closes = page.locator(".detail-close")
    assert toggles.count() == 2 and closes.count() >= 4
    for paint in toggles.evaluate_all(_PAINT) + closes.evaluate_all(_PAINT):
        assert paint == ["rgba(0, 0, 0, 0)", "0px"]


def _toast_clears_the_phone_nav(page):
    page.locator("#toast").evaluate(
        "(toast) => { toast.textContent = 'Model stopped'; toast.hidden = false; }"
    )
    toast_z = int(page.locator("#toast").evaluate("e => getComputedStyle(e).zIndex"))
    assert toast_z > 120, "toast must clear the floating nav's z-index: 120"


def test_hub_landing(browser, admin_url, browser_name):
    requested: list[str] = []
    with fresh_page(browser, console_errors=True, viewport=PHONE) as page:
        page.on("request", lambda r: requested.append(r.url.split("?")[0]))
        mark = lambda name: surface(page, name)  # noqa: E731 -- surface() must be called from the test body
        _boots_clean_into_hub(page, admin_url, requested)
        _opens_on_the_glance(page, mark, browser_name)
        _tiles_stack_under_a_heading(page)
        _empty_lists_name_their_control(page, mark)
        _labels_and_headers_are_plain(page)
        _toast_clears_the_phone_nav(page)
