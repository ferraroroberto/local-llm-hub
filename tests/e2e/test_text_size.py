"""The vendored text-size control in Settings (#673).

The home-head gear opens Settings; Small / Default / Large set
``html[data-textsize]``, which moves the root font-size, persist across a
reload, and scale the rem-based type on every tab without overflowing a phone
viewport.
"""

from __future__ import annotations

import pytest

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}

ROOT_PX = {"small": 15.0, "default": 16.0, "large": 18.0}

# tab button id -> pane id (OTel is reached from the header, not the nav).
_TABS = {
    "#tabHub": "#paneHub",
    "#tabModels": "#paneModels",
    "#tabPlayground": "#panePlayground",
    "#tabCodeUsage": "#paneCodeUsage",
    "#tabMachines": "#paneMachines",
}


def _root_px(page) -> float:
    return page.evaluate("parseFloat(getComputedStyle(document.documentElement).fontSize)")


def _title_px(page, pane: str) -> float:
    return page.evaluate(
        "pane => parseFloat(getComputedStyle("
        "document.querySelector(pane + ' .home-title')).fontSize)",
        pane,
    )


def _pick_size(page, pane: str, size: str) -> None:
    page.click(f"{pane} .home-settings")
    page.click(f"#textSizeControl [data-textsize='{size}']")
    page.click("#settingsCloseBtn")


def test_text_size_steps_change_the_root_and_persist(page, admin_url):
    page.set_viewport_size(PHONE)
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub .home-settings", state="visible", timeout=5000)

    assert _root_px(page) == ROOT_PX["default"]
    for size in ("small", "large", "default"):
        _pick_size(page, "#paneHub", size)
        assert _root_px(page) == ROOT_PX[size], size

    _pick_size(page, "#paneHub", "large")
    page.reload(wait_until="load")
    page.wait_for_selector("#paneHub .home-settings", state="visible", timeout=5000)
    assert _root_px(page) == ROOT_PX["large"]
    page.click("#paneHub .home-settings")
    active = page.locator("#textSizeControl .range-tab.active")
    assert active.get_attribute("data-textsize") == "large"


@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_large_text_scales_every_tab_without_overflow(page, admin_url, viewport, theme):
    page.set_viewport_size(viewport)
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub .home-settings", state="visible", timeout=5000)
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
    _pick_size(page, "#paneHub", "default")

    default = {}
    for tab, pane in _TABS.items():
        page.click(tab)
        page.wait_for_selector(f"{pane} .home-head", state="visible", timeout=5000)
        default[pane] = _title_px(page, pane)

    page.click("#tabHub")
    _pick_size(page, "#paneHub", "large")
    for tab, pane in _TABS.items():
        page.click(tab)
        page.wait_for_selector(f"{pane} .home-head", state="visible", timeout=5000)
        assert _title_px(page, pane) == pytest.approx(default[pane] * 18 / 16), pane
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"{pane} overflows by {overflow}px at Large"
