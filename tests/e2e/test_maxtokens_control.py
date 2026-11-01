"""The Play tab's Max tokens presets keep the vendored segmented geometry (#939).

A local ``align-items: stretch`` once grew the track to the number input's
height while each item kept its own, so the selected pill sat inside an
oversized track. The track is the item plus the 3px inset on both sides, and
the number input is sized to that track, never the reverse.
"""

from __future__ import annotations

import pytest

_SIZES = {"phone": (390, 844), "desktop": (1280, 900)}


@pytest.mark.parametrize("size", sorted(_SIZES))
def test_maxtokens_track_hugs_its_items(page, admin_url, size):
    page.set_viewport_size(
        {"width": _SIZES[size][0], "height": _SIZES[size][1]}
    )
    page.goto(admin_url, wait_until="load")
    page.click("#tabPlayground")
    page.wait_for_selector("#panePlayground", state="visible", timeout=3000)
    page.evaluate("document.getElementById('playgroundMore').open = true")
    page.wait_for_selector("#playgroundMaxTokensSeg", state="visible", timeout=3000)

    track = page.locator("#playgroundMaxTokensSeg").bounding_box()
    item = page.locator(
        '#playgroundMaxTokensSeg .segmented-item[aria-pressed="true"]'
    ).bounding_box()
    field = page.locator("#playgroundMaxTokens").bounding_box()

    assert abs(track["height"] - (item["height"] + 6)) <= 1, (track, item)
    assert abs(field["height"] - track["height"]) <= 1, (field, track)
