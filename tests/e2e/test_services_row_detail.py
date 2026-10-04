"""A Hub Services row reads in two lines; the long detail is one tap away (#647, J-10).

The design review's judgment pass answers no when a list row carries more than
two lines of text. The Services rows put a raw connection error or an
"out of sync <sha> vs <sha>" line beside the name and status, and on a phone it
wrapped to three or four lines. The face keeps name + status (+ actions) and one
truncated detail line; the full text opens from that line, nothing dropped.
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
    "peers": [
        {"host_id": "mac-mini-m4", "display_name": "Mac Mini M4", "reachable": False,
         "error": LONG_ERROR},
        {"host_id": "gaming", "display_name": "gaming", "reachable": True,
         "git_sha_match": False, "remote_git_sha": "3cdda9e2c56f", "local_git_sha": "18a15f7e2752"},
    ],
}

_ROW_FACTS = """() => [...document.querySelectorAll('#servicesCard .services-row')].map(row => {
    // Rendered lines of text in the row: the distinct line boxes its visible
    // text occupies, so the name and the status sharing a line count once.
    const tops = [];
    const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (!n.textContent.trim()) continue;
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


def _open_hub(page, admin_url, viewport):
    page.set_viewport_size(viewport)
    page.route(
        "**/admin/api/services/status*",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(_STATUS)),
    )
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#peerRows .services-row", state="visible", timeout=10000)
    page.wait_for_function(
        "document.getElementById('dockerStatusText').textContent !== '—'", timeout=10000
    )


@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_service_rows_read_in_two_lines_and_the_detail_opens(page, admin_url, viewport):
    _open_hub(page, admin_url, viewport)

    rows = page.evaluate(_ROW_FACTS)
    assert len(rows) == 5, rows
    for row in rows:
        assert row["lines"] <= 2, f"{row['name']} face is {row['lines']} lines: {row}"

    # Every long detail keeps its full text, one tap away on its own row.
    long_rows = page.locator("#servicesCard .services-row details.services-detail")
    assert long_rows.count() == 4, "docker, langfuse, agentsview and the down peer carry long detail"
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


def test_short_detail_stays_inline_without_a_disclosure(page, admin_url):
    _open_hub(page, admin_url, PHONE)
    # The out-of-sync peer's "<badge> sha vs sha" fits a line: plain text.
    row = page.locator('#peerRows .services-row[data-host-id="gaming"]')
    assert row.locator("details").count() == 0
    assert "out of sync" in row.inner_text()
