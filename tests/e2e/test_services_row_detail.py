"""Hub services: exceptions on the face, every row in the sheet.

The face lists only a stopped or failing service, as its own row with Start, and
the header line names it; the healthy ones fold into one row that opens the
services sheet. In the sheet each row reads in two lines: the name and status
(+ actions) and one truncated detail line whose full text opens from that line,
nothing dropped. A healthy service is plain words, never a coloured dot.
"""

from __future__ import annotations

from tests.e2e._flow import DESKTOP, PHONE, fresh_page, fulfil_json
from tests.e2e._surface import surface

LONG_ERROR = (
    "HTTPConnectionPool(host='127.0.0.1', port=3000): Max retries exceeded with "
    "url: /api/public/health (Caused by NewConnectionError: failed to establish "
    "a new connection: connection refused)"
)

_STATUS = {
    "launchable": True,
    "docker": {"running": False, "error": LONG_ERROR},
    "langfuse": {"reachable": False, "error": LONG_ERROR},
    "agentsview": {"host": "127.0.0.1", "reachable": False, "installed": True, "error": LONG_ERROR},
}

_HEALTHY = {
    "launchable": True,
    "docker": {"running": True, "server_version": "27.1.1"},
    "langfuse": {"reachable": True},
    "agentsview": {"host": "127.0.0.1", "reachable": True, "installed": True, "version": "0.9.2"},
}

_ROW_FACTS = """() => [...document.querySelectorAll('#servicesSheet .services-row')].map(row => {
    // Rendered lines of text in the row: the distinct line boxes its visible
    // text occupies, so the name and the status sharing a line count once.
    const tops = [];
    const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (!n.textContent.trim()) continue;
        // A closed disclosure's body is not rendered, so it is not a line.
        const closed = n.parentElement.closest('details:not([open])');
        if (closed && !n.parentElement.closest('summary')) continue;
        const g = document.createRange(); g.selectNodeContents(n);
        for (const r of g.getClientRects()) {
            if (r.width === 0 || r.height === 0) continue;
            if (!tops.some(t => Math.abs(t - r.top) < 4)) tops.push(r.top);
        }
    }
    const detail = row.querySelector('.services-detail');
    return { name: row.querySelector('.services-label').innerText.trim(), lines: tops.length,
             detailText: detail ? detail.textContent.trim() : '' };
})"""


def _open_hub(page, admin_url, status):
    page.route("**/admin/api/services/status*", lambda route: fulfil_json(route, status))
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#servicesList .services-summary", state="visible", timeout=10000)
    page.wait_for_function(
        "document.getElementById('dockerStatusText').textContent !== '—'", timeout=10000)
    # The header line is filled by a different read (/admin/api/status) and says
    # "Reading hub…" until it lands; on a slow runner the services rows can paint
    # first, so asserting on the line before this read the line, not the hub.
    page.wait_for_function(
        "!document.getElementById('hubHeadLine').textContent.startsWith('Reading hub')",
        timeout=10000,
    )


def _open_sheet(page):
    page.click("#servicesList .services-summary .action-row-main")
    page.wait_for_selector("#servicesSheet[open]", timeout=3000)


def _rows_read_in_two_lines_and_the_detail_opens(page, mark):
    """A stopped service is its own face row with Start, the header names it,
    each sheet row is at most two lines, and the long detail opens from the
    keyboard with its full text."""
    face = page.locator("#servicesList .service-row")
    assert face.count() == 3
    assert face.locator('[data-act="svc-start"]').count() == 3
    head = page.locator("#hubHeadLine").text_content()
    assert "Docker down" in head, head
    mark("hub.services-stopped")

    _open_sheet(page)
    rows = page.evaluate(_ROW_FACTS)
    assert len(rows) == 3, rows
    for row in rows:
        assert row["lines"] <= 2, f"{row['name']} face is {row['lines']} lines: {row}"
    mark("hub.services-sheet-stopped")

    long_rows = page.locator("#servicesSheet .services-row details.services-detail")
    assert long_rows.count() == 3, "docker, langfuse and agentsview carry long detail"
    first = long_rows.first
    summary = first.locator("summary")
    assert first.get_attribute("open") is None
    assert summary.evaluate("e => e.getBoundingClientRect().height") <= 48  # closed: one line, truncated
    summary.focus()
    page.keyboard.press("Enter")  # keyboard-reachable
    assert first.get_attribute("open") is not None
    assert "Max retries exceeded" in first.inner_text()
    assert LONG_ERROR in first.inner_text()


def _an_open_detail_holds_its_text_once(page):
    """The full text lives in the disclosure's body (an open disclosure is never
    an empty container); the ellipsised preview in the summary gives way to it,
    so the line shows once and never twice."""
    _open_sheet(page)
    detail = page.locator("#servicesSheet .services-row details.services-detail").first
    body = detail.locator(".services-detail-body")
    summary_text = detail.locator("summary .services-detail-text")

    assert summary_text.is_visible()  # closed: the one ellipsised preview line
    assert body.is_hidden()

    detail.locator("summary").click()
    assert detail.get_attribute("open") is not None
    assert body.is_visible()
    assert body.inner_text().strip() == LONG_ERROR
    assert summary_text.is_hidden()
    assert detail.inner_text().count("Max retries exceeded") == 1

    detail.locator("summary").click()  # the summary stays the tap target to close it
    assert detail.get_attribute("open") is None
    assert summary_text.is_visible()


def _a_poll_with_unchanged_text_leaves_a_detail_open(page):
    _open_sheet(page)
    detail = page.locator("#servicesSheet .services-row details.services-detail").first
    detail.locator("summary").click()
    assert detail.get_attribute("open") is not None

    # The hub polls services every 5 s; let one land and render.
    with page.expect_response("**/admin/api/services/status*", timeout=9000):
        pass
    page.wait_for_timeout(300)

    detail = page.locator("#servicesSheet .services-row details.services-detail").first
    assert detail.get_attribute("open") is not None, "the poll snapped the disclosure shut"
    assert detail.locator(".services-detail-body").inner_text().strip() == LONG_ERROR


def _healthy_services_fold_and_a_short_detail_stays_inline(page):
    assert page.locator("#servicesList .service-row").count() == 0
    assert "All up" in page.locator("#servicesList .services-summary").inner_text()
    assert "down" not in page.locator("#hubHeadLine").text_content()
    _open_sheet(page)
    # The engine version fits a line: plain text, no disclosure; healthy is words with no dot.
    docker = page.locator("#servicesSheet .services-row").first
    assert docker.locator("details").count() == 0
    assert "engine 27.1.1" in docker.inner_text()
    assert docker.locator(".dot").is_hidden()


def test_hub_services_sheet(browser, admin_url):
    for viewport in (PHONE, DESKTOP):
        with fresh_page(browser, viewport=viewport) as page:
            _open_hub(page, admin_url, _STATUS)
            _rows_read_in_two_lines_and_the_detail_opens(page, lambda name: surface(page, name))
    with fresh_page(browser, viewport=PHONE) as page:
        _open_hub(page, admin_url, _STATUS)
        _an_open_detail_holds_its_text_once(page)
    with fresh_page(browser, viewport=DESKTOP) as page:
        _open_hub(page, admin_url, _STATUS)
        _a_poll_with_unchanged_text_leaves_a_detail_open(page)
    with fresh_page(browser, viewport=PHONE) as page:
        _open_hub(page, admin_url, _HEALTHY)
        _healthy_services_fold_and_a_short_detail_stays_inline(page)
