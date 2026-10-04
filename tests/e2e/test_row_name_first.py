"""A request row leads with what was asked for, and the time trails (#647, J-10).

The design review's judgment pass answers no when a list's rows lead with a
timestamp instead of the row's name. The Hub's live-request and error lists and
the Telemetry trace feed all opened a row with its clock; the model now leads
and the clock is the last, muted cell.
"""

from __future__ import annotations

import re

import httpx

CLOCK = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?")
MARKER = "e2e-name-first-model"


def _post_bogus_request(admin_url: str) -> None:
    base = admin_url.rsplit("/admin/", 1)[0]
    r = httpx.post(
        f"{base}/v1/messages",
        json={"model": MARKER, "messages": [{"role": "user", "content": "hi"}]},
        timeout=5.0,
    )
    assert r.status_code == 400, r.text


def _first_and_last_cells(page, list_selector: str):
    page.wait_for_function(
        "([sel, m]) => (document.querySelector(sel)?.textContent || '').includes(m)",
        arg=[list_selector, MARKER],
        timeout=10000,
    )
    return page.evaluate(
        """([sel, m]) => {
            const li = [...document.querySelectorAll(sel + ' > li')]
                .find(l => (l.textContent || '').includes(m));
            const cells = [...li.children].map(c => (c.textContent || '').trim());
            return { first: cells[0], last: cells[cells.length - 1], all: cells.join(' ') };
        }""",
        [list_selector, MARKER],
    )


def test_hub_request_and_error_rows_lead_with_the_model(page, admin_url):
    _post_bogus_request(admin_url)
    page.goto(admin_url, wait_until="load")
    for selector in ("#liveRequestsList", "#recentErrorsList"):
        cells = _first_and_last_cells(page, selector)
        assert MARKER in cells["first"], f"{selector} row opens with {cells['first']!r}"
        assert CLOCK.match(cells["last"]), f"{selector} row ends with {cells['last']!r}"


def test_trace_feed_rows_lead_with_the_model(page, admin_url):
    _post_bogus_request(admin_url)
    page.goto(admin_url, wait_until="load")
    page.click('[data-open-tab="telemetry"]')
    cells = _first_and_last_cells(page, "#telTracesList")
    assert MARKER in cells["first"], f"trace row opens with {cells['first']!r}"
    # The trace row's trailing cells are its meta and actions blocks; the clock
    # is still in the row, just not where the eye starts.
    assert re.search(r"\d{1,2}:\d{2}:\d{2}", cells["all"]), cells["all"]
