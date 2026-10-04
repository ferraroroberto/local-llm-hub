"""On a phone, tiles stack and each screen opens with a heading (#647, J-04).

The design review's judgment pass flags content blocks that share a horizontal
band at phone width (the Hub load tiles and the Code counters sat 2x2) and a
screen whose first text under the top bar is a data line rather than a heading
(the Hub card opened on "PID ... Uptime").
"""

from __future__ import annotations

PHONE = {"width": 390, "height": 844}

_SIDE_BY_SIDE = """sel => {
  const r = [...document.querySelectorAll(sel)].map(e => e.getBoundingClientRect())
    .filter(b => b.width > 0 && b.height > 0);
  let pairs = 0;
  for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
    const overlapY = Math.min(r[i].bottom, r[j].bottom) - Math.max(r[i].top, r[j].top);
    if (overlapY > 1) pairs++;
  }
  return {count: r.length, sideBySide: pairs};
}"""


def test_hub_load_tiles_stack_under_a_heading(page, admin_url):
    page.set_viewport_size(PHONE)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#hubSparklines .sparkline", state="visible", timeout=5000)

    first_card = page.locator("#paneHub .home-head + section.card")
    assert first_card.locator(":scope > .card-header h2").count() == 1, (
        "the first Hub card should open on a heading, not on the PID/uptime line"
    )

    got = page.evaluate(_SIDE_BY_SIDE, "#hubSparklines .sparkline")
    assert got["count"] >= 2, got
    assert got["sideBySide"] == 0, f"Hub load tiles share a row on a phone: {got}"


def test_code_counters_stack_on_a_phone(page, admin_url):
    page.set_viewport_size(PHONE)
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#cldCounterGrid .cld-counter", state="visible", timeout=10000)

    got = page.evaluate(_SIDE_BY_SIDE, "#cldCounterGrid .cld-counter")
    assert got["count"] == 4, got
    assert got["sideBySide"] == 0, f"Code counters share a row on a phone: {got}"
