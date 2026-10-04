"""The Code tab says it is loading when a vendor or period switch refetches (#693).

Every request to the usage summary is held by the test until it chooses to
answer, so the in-flight window is observable. Pins:

  * a switch shows the frosted "Loading usage…" toast until the data lands, then
    dismisses it;
  * rapid clicks fire one request, for the final choice, and the cards show that
    choice's data; a click after the request is out aborts the stale one;
  * the background poll never shows the toast;
  * a failed fetch shows the error toast and keeps the #580 error banner.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.usefixtures("admin_url")

PANE_TIMEOUT = 10000
# One distinct request count per period, so the cards prove which choice's
# data they are showing.
REQUESTS = {"today": 1, "week": 7, "month": 30, "all": 99}


def _body(period: str, vendor: str = "all") -> str:
    return json.dumps({
        "period": period, "vendor": vendor,
        "totals": {
            "requests": REQUESTS[period], "input_tokens": 1000,
            "output_tokens": 500, "cache_read_tokens": 100,
            "cache_creation_tokens": 0, "input_cost": 1, "output_cost": 1,
            "cache_read_cost": 0,
        },
        "daily": [], "by_model": [], "by_project": [], "by_vendor": [],
        "recent_sessions": [], "time_series": [],
        "agentsview": {"enabled": False, "reachable": False, "vendors": []},
    })


def _query(route):
    q = parse_qs(urlparse(route.request.url).query)
    return q["period"][0], q["vendor"][0]


class Summary:
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
        route.fulfill(status=200, content_type="application/json", body=_body(period, vendor))

    def answer_last(self):
        route = self.held.pop()
        period, vendor = _query(route)
        route.fulfill(status=200, content_type="application/json", body=_body(period, vendor))


def _open(page, admin_url):
    summary = Summary(page)
    page.set_viewport_size({"width": 800, "height": 900})
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)
    page.wait_for_function(
        "document.getElementById('cldRequests').textContent === '1'", timeout=PANE_TIMEOUT
    )
    summary.hold = True
    return summary


def _click(page, selector):
    page.evaluate(f"document.querySelector('{selector}').click()")


def _wait_held(page, summary, n):
    page.wait_for_function("() => true")           # let queued events run
    for _ in range(100):
        if len(summary.held) >= n:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"expected {n} held request(s), saw {len(summary.held)}")


def test_switch_shows_loading_toast_until_data_lands(page, admin_url):
    summary = _open(page, admin_url)
    toast = page.locator("#toast")
    assert toast.is_hidden()

    _click(page, '#cldPeriodSeg button[data-period="week"]')
    toast.wait_for(state="visible", timeout=2000)
    assert toast.inner_text() == "Loading usage…"
    _wait_held(page, summary, 1)
    # Still loading: the toast does not auto-hide on its 2.2 s timer.
    page.wait_for_timeout(2600)
    assert toast.is_visible()

    summary.answer_last()
    toast.wait_for(state="hidden", timeout=5000)
    page.wait_for_function(
        "document.getElementById('cldRequests').textContent === '7'", timeout=PANE_TIMEOUT
    )


def test_toast_is_the_frosted_neutral_glass(page, admin_url):
    _open(page, admin_url)
    _click(page, '#cldVendorSeg button[data-vendor="codex"]')
    page.locator("#toast").wait_for(state="visible", timeout=2000)
    style = page.locator("#toast").evaluate(
        "e => { const s = getComputedStyle(e); return { f: s.backdropFilter || s.webkitBackdropFilter, w: s.fontWeight }; }"
    )
    assert "blur" in style["f"], style
    assert int(style["w"]) >= 700, style


def test_rapid_clicks_fire_one_request_for_the_final_choice(page, admin_url):
    summary = _open(page, admin_url)
    before = len(summary.seen)

    for period in ("week", "month", "all"):
        _click(page, f'#cldPeriodSeg button[data-period="{period}"]')
    _wait_held(page, summary, 1)
    page.wait_for_timeout(600)

    fired = summary.seen[before:]
    assert fired == [("all", "all")], f"expected one request for the final choice, got {fired}"
    summary.answer_last()
    page.locator("#toast").wait_for(state="hidden", timeout=5000)
    page.wait_for_function(
        "document.getElementById('cldRequests').textContent === '99'", timeout=PANE_TIMEOUT
    )
    assert page.evaluate(
        "document.querySelector('#cldPeriodSeg button.active').dataset.period"
    ) == "all"


def test_a_click_after_the_request_is_out_aborts_the_stale_one(page, admin_url):
    summary = _open(page, admin_url)

    _click(page, '#cldPeriodSeg button[data-period="week"]')
    _wait_held(page, summary, 1)                    # the week request is on the wire
    _click(page, '#cldPeriodSeg button[data-period="month"]')
    _wait_held(page, summary, 2)
    for _ in range(40):
        if summary.failed:
            break
        page.wait_for_timeout(50)
    assert summary.failed, "the stale week request was not aborted"

    summary.answer_last()                           # the month request
    page.locator("#toast").wait_for(state="hidden", timeout=5000)
    page.wait_for_function(
        "document.getElementById('cldRequests').textContent === '30'", timeout=PANE_TIMEOUT
    )


def test_background_poll_shows_no_toast(page, admin_url):
    page.clock.install()
    summary = Summary(page)
    page.set_viewport_size({"width": 800, "height": 900})
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#paneCodeUsage", state="visible", timeout=PANE_TIMEOUT)
    page.wait_for_function(
        "document.getElementById('cldRequests').textContent === '1'", timeout=PANE_TIMEOUT
    )
    summary.hold = True
    before = len(summary.seen)

    page.clock.fast_forward(31_000)                 # past the 30 s poll interval
    _wait_held(page, summary, 1)
    assert len(summary.seen) > before, "the poll did not fetch"
    assert page.locator("#toast").is_hidden()
    summary.answer_last()
    page.wait_for_timeout(300)
    assert page.locator("#toast").is_hidden()


@pytest.mark.expect_console_errors
def test_failed_switch_shows_error_toast_and_keeps_the_banner(page, admin_url):
    summary = _open(page, admin_url)

    _click(page, '#cldPeriodSeg button[data-period="week"]')
    _wait_held(page, summary, 1)
    summary.held.pop().fulfill(
        status=503, content_type="application/json",
        body='{"period":"week","vendor":"all","error":"transcript store unreadable",'
             '"detail":"Could not build the code-usage summary: transcript store unreadable"}',
    )

    page.wait_for_selector("#cldError", state="visible", timeout=PANE_TIMEOUT)
    toast = page.locator("#toast")
    toast.wait_for(state="visible", timeout=2000)
    assert "error" in (toast.get_attribute("class") or "")
    assert "Loading usage" not in toast.inner_text()
    assert "transcript store unreadable" in page.inner_text("#cldErrorMsg")
