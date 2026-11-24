"""The admin SPA's main page keeps the fleet request budget while it sits open.

One page on a fake clock counts the admin API requests of a visible minute, of a
hidden minute and of the return to the foreground. The budget is the app's
`[request_budget] visible_requests_per_minute` in `.fleet.toml`; a hidden tab
sends nothing, and the return reads each path at most once. The probe page runs
in its own context because `page.clock.install()` is a context-wide change the
shared contexts of the other flows must not inherit.

The Hub tab's two live streams (request feed, log tail) are server-sent event
connections that stay open by design, and the probe waits for every request to
finish before it advances the clock. Each is answered with a finished stream
whose `retry` outlasts the run: opening it is still counted, which is what the
budget cares about, and the page's own close on hide and reopen on return still
runs.

One hidden-state request is allowed and named: the usage log's `session_end`
beacon, sent once at the moment the tab hides because that is the last chance to
report the visit. It does not repeat, so the rest of the hidden minute must stay
at zero.

Runs under Chromium only; `page.clock` needs Playwright 1.45+.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Route

from tests.e2e._flow import PHONE, PHASE_TIMEOUT_MS
from tests.e2e.budget._request_budget import assert_request_budget, measure_request_budget, visible_budget

REPO_ROOT = Path(__file__).resolve().parents[2]

_STREAMS = ("**/admin/api/hub/requests/stream", "**/admin/api/hub/log/tail")
# The usage log's one send at hide (`session_end`); see the module docstring.
_HIDE_BEACON = "/admin/api/usage/events"
# An hour, in ms: longer than the two fake minutes plus the real time the probe takes.
_NO_RECONNECT = "retry: 3600000\n\n"


def _end_stream(route: Route) -> None:
    route.fulfill(status=200, content_type="text/event-stream", body=_NO_RECONNECT)


def test_main_page_keeps_its_request_budget(browser, hub_url: str) -> None:
    context = browser.new_context(viewport=PHONE)
    try:
        page = context.new_page()
        page.set_default_timeout(PHASE_TIMEOUT_MS)
        page.set_default_navigation_timeout(PHASE_TIMEOUT_MS)
        for pattern in _STREAMS:
            page.route(pattern, _end_stream)
        reading = measure_request_budget(page, f"{hub_url}/admin/", api_prefix="/admin/api/")
        beacons = reading.hidden.pop(_HIDE_BEACON, 0)
        assert beacons <= 1, f"the usage log beacons once at hide, not {beacons} times: {reading.summary()}"
        assert_request_budget(reading, visible_budget(REPO_ROOT))
    finally:
        context.close()
