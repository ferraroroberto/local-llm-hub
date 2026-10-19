"""A Models row's ⋮ menu stays reachable on a phone (#767, #772).

At 390×844 the floating nav pill covers the bottom of the screen. A menu on the
last visible row used to open downward, behind the pill and past the viewport,
so its items could not be tapped. The menu is now the vendored row-menu: it
opens upward when below would not fit, sits in front of the pill, re-fits while
open, and stays anchored to its row while the 5 s poll rebuilds the list. Its
rows use the fleet menu's size (body-size text, 44px rows) so the hub and
app-launcher match.

Invented models over route interception; no real backend is started or stopped.
"""

from __future__ import annotations

import json

from tests.e2e._surface import surface

PHONE = {"width": 390, "height": 844}

MODELS = {"models": [
    {
        "id": f"demo_{i:02d}", "display_name": f"demo-{i:02d}", "backend": "llama",
        "engine": "llama-server", "port": 9100 + i, "url": None, "aliases": [],
        "controllable": True, "ownership": "none", "pid": None,
        "reachable": False, "model_path": None, "host": "tower",
        "preferred_host": "tower", "failover": False,
    }
    for i in range(14)
]}

_GEOMETRY = """() => {
  const r = e => { const b = e.getBoundingClientRect(); return {top: b.top, bottom: b.bottom}; };
  const list = document.querySelector('body > .row-menu');
  const toggle = document.querySelector('.row-menu-anchor[aria-expanded="true"]');
  const nav = document.querySelector('nav.tabs');
  const b = list.getBoundingClientRect();
  const hit = document.elementFromPoint(b.left + b.width / 2, b.bottom - 6);
  const item = list.querySelector('.row-menu-item');
  const cs = getComputedStyle(item);
  return {
    list: r(list), toggle: r(toggle), nav: r(nav), vh: window.innerHeight,
    frontMost: !!hit && list.contains(hit),
    fontPx: parseFloat(cs.fontSize), itemMinPx: parseFloat(cs.minHeight),
    glyphPx: item.querySelector('svg').getBoundingClientRect().width,
  };
}"""


def _json(route, body):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def _open_phone_models(browser, admin_url, models=MODELS):
    """Open the Models tab on a phone once its list has settled.

    Opening the tab reads fleet-placement, and its landing repaints every row
    (models.js). Left to land on its own, that repaint can replace a row the
    test is already acting on (#829), so the read is held until the rows are
    painted, then released, and the rows it replaces are waited out.
    """
    context = browser.new_context(viewport=PHONE, has_touch=True)
    page = context.new_page()
    held = []
    page.add_init_script("localStorage.setItem('llmhub.models.activeOnly', 'false');")
    page.route("**/admin/api/models", lambda route: _json(route, models))
    page.route("**/admin/api/fleet-placement", lambda route: held.append(route))
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#modelsList .model-row", state="visible", timeout=10000)
    for _ in range(100):  # the route handler runs while Playwright waits
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "opening the Models tab should read fleet-placement"
    page.evaluate("window.__preRepaintRow = document.querySelector('#modelsList .model-row')")
    page.unroute("**/admin/api/fleet-placement")
    page.route("**/admin/api/fleet-placement", lambda route: _json(route, {"hosts": []}))
    for route in held:
        _json(route, {"hosts": []})
    page.wait_for_function("!window.__preRepaintRow.isConnected", timeout=10000)
    return context, page


def test_last_row_menu_opens_upward_in_front_of_the_nav_pill(browser, admin_url):
    context, page = _open_phone_models(browser, admin_url)
    try:
        last = page.locator("#modelsList .model-row").last
        last.scroll_into_view_if_needed()
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        last.locator(".row-kebab").click()
        page.wait_for_selector("body > .row-menu .row-menu-item", state="visible", timeout=5000)
        page.wait_for_timeout(150)  # the re-fit runs on the next frame

        g = page.evaluate(_GEOMETRY)
        assert g["nav"]["top"] > 0, "the phone nav pill should be showing"
        assert g["list"]["top"] >= 0, g
        assert g["list"]["bottom"] <= g["nav"]["top"], f"menu runs under the pill: {g}"
        assert g["list"]["bottom"] <= g["toggle"]["top"] + 1, f"menu should open upward: {g}"
        assert g["frontMost"], f"the pill is painted over the menu: {g}"

        # It re-fits while open: a scroll moves the row, the menu keeps clear of the pill.
        page.mouse.wheel(0, -120)
        page.wait_for_timeout(250)
        g = page.evaluate(_GEOMETRY)
        assert 0 <= g["list"]["top"] and g["list"]["bottom"] <= g["nav"]["top"], g
    finally:
        context.close()


def test_first_row_menu_opens_downward_and_uses_the_fleet_menu_size(browser, admin_url):
    context, page = _open_phone_models(browser, admin_url)
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.locator("#modelsList .model-row").first.locator(".row-kebab").click()
        page.wait_for_selector("body > .row-menu .row-menu-item", state="visible", timeout=5000)
        page.wait_for_timeout(150)

        g = page.evaluate(_GEOMETRY)
        assert g["list"]["top"] >= g["toggle"]["bottom"] - 1, f"room below: stay below: {g}"
        assert g["list"]["bottom"] <= g["nav"]["top"], g
        assert g["fontPx"] == 16, f"menu text should be body size: {g}"
        assert g["itemMinPx"] == 44 and g["glyphPx"] == 16, g
        surface(page, "models.row-menu-phone")
    finally:
        context.close()


def test_open_menu_stays_anchored_while_the_models_list_refreshes(browser, admin_url):
    """The poll rebuilds every row; the open menu follows its row to the new
    kebab instead of closing or parking in a corner (project-scaffolding#373)."""
    live = json.loads(json.dumps(MODELS))
    context, page = _open_phone_models(browser, admin_url, live)
    try:
        page.locator("#modelsList .model-row").nth(2).locator(".row-kebab").click()
        page.wait_for_selector("body > .row-menu .row-menu-item", state="visible", timeout=5000)
        page.evaluate("window.__oldKebab = document.querySelector('.row-menu-anchor[aria-expanded=\"true\"]')")

        live["models"][0]["port"] = 9199  # changes the list's markup, so the next poll repaints
        page.wait_for_function("window.__oldKebab && !window.__oldKebab.isConnected", timeout=15000)
        page.wait_for_timeout(300)  # the held menu re-places on the next frame

        g = page.evaluate(_GEOMETRY)
        assert page.locator("body > .row-menu").count() == 1, "the open menu survived the repaint"
        assert abs(g["list"]["top"] - g["toggle"]["bottom"] - 2) <= 1 or abs(g["list"]["bottom"] - g["toggle"]["top"] + 2) <= 1, (
            f"menu is not beside its new kebab: {g}")
        assert g["list"]["bottom"] <= g["nav"]["top"], g
        assert g["frontMost"], g
    finally:
        context.close()
