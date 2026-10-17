"""Hub services: exceptions on the face, every row in the sheet (#738; #647, J-10).

The face lists only a stopped or failing service, as its own row with Start, and
the header line names it; the healthy ones fold into one row that opens the
services sheet. In the sheet each row reads in two lines: the name and status
(+ actions) and one truncated detail line whose full text opens from that line,
nothing dropped. A healthy service is plain words, never a coloured dot.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.usefixtures("admin_url")

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}

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


def _open_hub(page, admin_url, viewport, status=_STATUS):
    page.set_viewport_size(viewport)
    page.route(
        "**/admin/api/services/status*",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(status)),
    )
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#servicesList .services-summary", state="visible", timeout=10000)
    page.wait_for_function(
        "document.getElementById('dockerStatusText').textContent !== '—'", timeout=10000
    )
    # The header line is filled by a different read (/admin/api/status) and says
    # "Reading hub…" until it lands; on a slow runner the services rows can paint
    # first, so asserting on the line before this read the line, not the hub (#752).
    page.wait_for_function(
        "!document.getElementById('hubHeadLine').textContent.startsWith('Reading hub')",
        timeout=10000,
    )


def _open_sheet(page):
    page.click("#servicesList .services-summary .action-row-main")
    page.wait_for_selector("#servicesSheet[open]", timeout=3000)


@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_service_rows_read_in_two_lines_and_the_detail_opens(page, admin_url, viewport):
    _open_hub(page, admin_url, viewport)

    # A stopped service is its own face row with Start, and the header names it.
    face = page.locator("#servicesList .service-row")
    assert face.count() == 3
    assert face.locator('[data-act="svc-start"]').count() == 3
    head = page.locator("#hubHeadLine").text_content()
    assert "Docker down" in head, head

    _open_sheet(page)
    rows = page.evaluate(_ROW_FACTS)
    assert len(rows) == 3, rows
    for row in rows:
        assert row["lines"] <= 2, f"{row['name']} face is {row['lines']} lines: {row}"

    # Every long detail keeps its full text, one tap away on its own row.
    long_rows = page.locator("#servicesSheet .services-row details.services-detail")
    assert long_rows.count() == 3, "docker, langfuse and agentsview carry long detail"
    first = long_rows.first
    summary = first.locator("summary")
    assert first.get_attribute("open") is None
    # Closed: one line, truncated.
    assert summary.evaluate("e => e.getBoundingClientRect().height") <= 48
    summary.focus()
    page.keyboard.press("Enter")                    # keyboard-reachable
    assert first.get_attribute("open") is not None
    assert "Max retries exceeded" in first.inner_text()
    assert LONG_ERROR in first.inner_text()


def test_an_open_long_detail_holds_its_text_in_the_body_once(page, admin_url):
    # #817: the full text lives in the disclosure's body (design.md: an open
    # disclosure is never an empty container); the ellipsised preview in the
    # summary gives way to it, so the line shows once and never twice.
    _open_hub(page, admin_url, PHONE)
    _open_sheet(page)
    detail = page.locator("#servicesSheet .services-row details.services-detail").first
    body = detail.locator(".services-detail-body")
    summary_text = detail.locator("summary .services-detail-text")

    # Closed: the one ellipsised preview line; the body is not shown.
    assert summary_text.is_visible()
    assert body.is_hidden()

    detail.locator("summary").click()
    assert detail.get_attribute("open") is not None
    assert body.is_visible()
    assert body.inner_text().strip() == LONG_ERROR
    assert summary_text.is_hidden()
    # Once: the text is not outside the body and visible in the summary as well.
    assert detail.inner_text().count("Max retries exceeded") == 1

    # The summary stays the tap target to close it again.
    detail.locator("summary").click()
    assert detail.get_attribute("open") is None
    assert summary_text.is_visible()


def test_a_poll_with_unchanged_text_leaves_an_open_detail_open(page, admin_url):
    _open_hub(page, admin_url, DESKTOP)
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


def test_healthy_services_fold_and_a_short_detail_stays_inline(page, admin_url):
    _open_hub(page, admin_url, PHONE, _HEALTHY)
    # All up: one folded row, no failing row, no service named in the header.
    assert page.locator("#servicesList .service-row").count() == 0
    assert "All up" in page.locator("#servicesList .services-summary").inner_text()
    assert "down" not in page.locator("#hubHeadLine").text_content()
    _open_sheet(page)
    # The engine version fits a line: plain text, no disclosure; healthy is
    # words with no dot.
    docker = page.locator("#servicesSheet .services-row").first
    assert docker.locator("details").count() == 0
    assert "engine 27.1.1" in docker.inner_text()
    assert docker.locator(".dot").is_hidden()
