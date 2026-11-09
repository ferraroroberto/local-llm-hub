"""The Models tab's list: what it opens on, what a row's actions do, how they look.

Invented models over route interception, so no real backend is started or
stopped. Each phase runs in its own browser context.

* The tab opens on the memory glance, then the list; Running · All follows the
  stored filter and survives a reload.
* A row's one visible verb is a plain glyph-only icon-button naming the row; Ping
  lives in the row's menu; Force stop confirms in the vendored dialog; Model log
  opens the backend's log tail; the roles reach each model's sheet.
* No verb is filled or outlined at rest, in either theme at phone and desktop.
* The row menu stays reachable on a phone: upward in front of the nav pill for
  the last row, downward for the first, anchored to its row across a repoll.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from tests.e2e._flow import DESKTOP, PHONE, fresh_page, fulfil_json, snapshot
from tests.e2e._surface import surface

_SCOPE = "#modelsScope"
_STORE_KEY = "llmhub.models.activeOnly"
_SHOW_ALL = "localStorage.setItem('llmhub.models.activeOnly', 'false');"


def _llama_row(row_id: str, port: int, *, ownership: str, pid, reachable: bool) -> dict:
    return {
        "id": row_id, "display_name": row_id.replace("_", "-"), "backend": "llama",
        "engine": "llama-server", "port": port, "url": None, "aliases": [],
        "controllable": True, "ownership": ownership, "pid": pid,
        "reachable": reachable, "model_path": None, "host": "tower",
        "preferred_host": "tower", "failover": False,
    }


ROWS = {"models": [
    _llama_row("running_row", 8081, ownership="ours", pid=4242, reachable=True),
    _llama_row("stopped_row", 8082, ownership="none", pid=None, reachable=False),
    _llama_row("adopted_row", 8083, ownership="external", pid=5151, reachable=True),
]}

# One stopped model and nothing running: Running is empty, All is not.
STOPPED_ONLY = {"models": [{
    "id": "stopped_row", "display_name": "stopped-row", "backend": "llama",
    "port": 8082, "aliases": [], "controllable": True, "ownership": "none",
    "pid": None, "reachable": False, "host": "tower",
}]}

# Two running models, so the default Running scope lists both.
ROLE_MODELS = {"models": [
    {
        "id": model_id, "display_name": name, "backend": "llama", "port": port,
        "aliases": [], "controllable": True, "ownership": "ours", "pid": 4242,
        "reachable": True, "host": "tower",
    }
    for model_id, name, port in (("qwen35_4b", "Qwen3.5 4B", 8082), ("whisper", "Whisper Turbo", 8090))
]}
ROLES = {"roles": {
    "agentic_light": {"model_id": "qwen35_4b", "display_name": "Qwen3.5 4B",
                      "notes": "fast lane", "fallback": []},
    "audio.transcribe": {"model_id": "parakeet", "display_name": "Parakeet",
                         "notes": None, "fallback": ["whisper"]},
}}
ROLE_PLACEMENT = {
    "placement": {"tower": ["whisper"]},
    "hosts": [{
        "id": "tower", "display_name": "Tower", "icon": "monitor",
        "local": True, "reachable": True, "can_ssh": False, "runs_hub": True,
        "eligible": [{"id": "whisper", "display_name": "Whisper Turbo"}],
        "placed": ["whisper"], "running": ["whisper"],
    }],
}

DEMO_ROWS = {"models": [
    _llama_row(f"demo_{i:02d}", 9100 + i, ownership="none", pid=None, reachable=False) | {
        "display_name": f"demo-{i:02d}"}
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


def _assert_scope(page, running: bool):
    seg = page.locator(_SCOPE)
    expect(seg.locator('[data-scope="running"]')).to_have_attribute("aria-pressed", "true" if running else "false")
    expect(seg.locator('[data-scope="all"]')).to_have_attribute("aria-pressed", "false" if running else "true")
    empty = page.locator("#modelsEmpty")
    if running:
        expect(empty).to_be_visible()
        expect(empty).to_contain_text("choose All")
        expect(page.locator('#modelsList .model-row[data-id="stopped_row"]')).to_have_count(0)
    else:
        expect(empty).to_be_hidden()
        expect(page.locator('#modelsList .model-row[data-id="stopped_row"]')).to_have_count(1)


def _running_all_follows_the_filter_and_persists(page, admin_url):
    # A preference stored by the old switch ("false" = show everything) is honoured.
    page.add_init_script(
        f"try {{ if (!sessionStorage.getItem('seeded')) {{ localStorage.setItem('{_STORE_KEY}', 'false');"
        " sessionStorage.setItem('seeded', '1'); } } catch (_) {}"
    )
    page.route("**/admin/api/models", lambda route: fulfil_json(route, STOPPED_ONLY))
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector(_SCOPE, state="visible", timeout=5000)
    _assert_scope(page, running=False)
    assert page.locator("#modelsActiveToggle").count() == 0, "the old switch is gone"

    page.click(f'{_SCOPE} [data-scope="running"]')
    _assert_scope(page, running=True)
    assert page.evaluate(f"localStorage.getItem('{_STORE_KEY}')") == "true"

    page.click(f'{_SCOPE} [data-scope="all"]')
    _assert_scope(page, running=False)

    page.click(f'{_SCOPE} [data-scope="running"]')  # Running, then reload: the choice persists
    page.reload(wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector(_SCOPE, state="visible", timeout=5000)
    _assert_scope(page, running=True)


def _the_tab_opens_on_the_glance_and_a_row_pings_from_its_menu(page, admin_url, browser_name, mark):
    """The tab opens on the memory glance, then the list; its header line is live,
    never a fixed slogan. Ping is offered on every row, so ask for All and the
    test no longer depends on what the host runs."""
    page.add_init_script(f"try {{ {_SHOW_ALL} }} catch (_) {{}}")
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#paneModels", state="visible", timeout=3000)
    order = page.evaluate(
        "() => [...document.querySelectorAll('#paneModels > .card, #paneModels > .segmented')].map(e => e.id)"
    )
    assert order.index("modelsMemoryCard") < order.index("modelsCard"), order
    page.wait_for_function(
        "() => !document.getElementById('modelsHeadLine').textContent.startsWith('Reading')", timeout=5000)
    snapshot(page, "models-390x844", browser_name)
    mark("models.tab")

    try:
        page.wait_for_selector("#modelsList .model-row", state="attached", timeout=10000)
    except Exception as exc:  # noqa: BLE001 -- name the missing element, then re-raise
        empty = page.locator("#modelsEmpty").text_content() or ""
        raise AssertionError(
            f"no '#modelsList .model-row' rendered on the Models tab (empty-state={empty.strip()!r})"
        ) from exc
    # Not a visible verb on the row: Start / Stop is its only one.
    assert page.locator('#modelsList .action-row-verb[data-act="ping"]').count() == 0
    offered = 0
    kebabs = page.locator("#modelsList .row-kebab")
    for i in range(kebabs.count()):
        kebabs.nth(i).click()
        page.wait_for_selector("body > .row-menu", state="visible", timeout=5000)
        ping = page.locator('body > .row-menu .row-menu-item[data-act="ping"]')
        if ping.count():
            offered += 1
            assert (ping.first.text_content() or "").strip() == "Ping"
        page.keyboard.press("Escape")
    assert offered > 0, "no row offers Ping from its More actions menu"


def _a_row_verb_is_a_named_glyph_and_its_menu_acts(page, admin_url):
    page.add_init_script(_SHOW_ALL)
    fleet_reads = []
    started = []
    page.route("**/admin/api/models", lambda route: fulfil_json(route, ROWS))
    page.route("**/admin/api/fleet-placement",
               lambda route: (fleet_reads.append(1), fulfil_json(route, {"hosts": []}))[-1])
    page.route("**/admin/api/models/stopped_row/start",
               lambda route: (started.append(1), fulfil_json(route, {"ok": True}))[-1])
    page.route("**/admin/api/models/running_row/log*",
               lambda route: fulfil_json(route, {"id": "running_row", "lines": ["demo line one", "demo line two"]}))
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#modelsList .model-row", state="visible", timeout=10000)

    for row_id, word, name, glyph in (
        ("running_row", "Stop", "running-row", "i-square"),
        ("stopped_row", "Start", "stopped-row", "i-play"),
    ):
        act = word.lower()
        btn = page.locator(f'#modelsList .model-row[data-id="{row_id}"] .action-row-verb[data-act="{act}"]')
        assert btn.count() == 1, f"{row_id} should carry exactly one power control"
        assert (btn.inner_text() or "").strip() == "", f"{row_id}: the row verb is glyph-only"
        assert btn.get_attribute("aria-label") == f"{word} {name}"
        assert btn.get_attribute("title") == f"{word} {name}", "the tooltip names the row"
        assert btn.get_attribute("data-track") == f"model-{act}"
        assert btn.locator(f'use[href="#{glyph}"]').count() == 1, f"{row_id}: wrong glyph"
        box = btn.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44, f"{row_id}: target under 44px {box}"

    # Start, then the memory glance is read once more after the action settles.
    for _ in range(50):  # the tab-open read lands first
        if fleet_reads:
            break
        page.wait_for_timeout(100)
    reads_before = len(fleet_reads)
    page.locator('#modelsList .model-row[data-id="stopped_row"] .action-row-verb[data-act="start"]').click()
    page.wait_for_timeout(6500)
    assert started, "Start did not POST"
    assert len(fleet_reads) == reads_before + 1, (
        f"memory should be read exactly once after Start, got {len(fleet_reads) - reads_before}"
    )

    # Force stop: no port or process id on the row face; the vendored confirm,
    # never the browser's own confirm().
    adopted = page.locator('#modelsList .model-row[data-id="adopted_row"]')
    face = adopted.locator(".action-row-main").inner_text()
    assert "5151" not in face and "PID" not in face
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
    adopted.locator(".row-kebab").click()
    items = page.locator("body > .row-menu .row-menu-item")
    assert items.last.get_attribute("data-act") == "force-stop", "Force stop is the menu's last item"
    assert "row-menu-danger" in (items.last.get_attribute("class") or "")
    items.last.click()
    page.wait_for_selector("#confirmDialog[open]", timeout=5000)
    assert "Force stop" in page.locator("#confirmDialogTitle").inner_text()
    assert not dialogs, "Force stop must not use the browser's confirm()"
    page.click("#confirmDialogCloseBtn")

    # Model log opens the backend's log tail.
    running = page.locator('#modelsList .model-row[data-id="running_row"]')
    running.locator(".row-kebab").click()
    page.locator('body > .row-menu .row-menu-item[data-act="log"]').click()
    page.wait_for_selector(".model-log-tail", state="visible", timeout=5000)
    page.wait_for_function(
        "() => document.querySelector('.model-log-tail').textContent.includes('demo line two')", timeout=5000)


def _row_verbs_are_unpainted_at_rest(page, admin_url):
    """The Models-row Start and Stop are the same plain icon-button, told apart by
    glyph and name alone, in both themes at phone and desktop width. The wide
    detail pane's head is not a list row: its Stop stays outlined, drawn the way
    the Services sheet draws its Stop."""
    def read(selector):
        # Resolved at call time: the 5 s poll may repaint the row between steps.
        page.wait_for_selector(selector, state="visible", timeout=5000)
        return page.evaluate(
            "(sel) => { const s = getComputedStyle(document.querySelector(sel));"
            " return [s.backgroundColor, s.borderTopWidth, s.borderTopStyle, s.boxShadow]; }", selector)

    def paint(selector):
        return page.evaluate(
            "(sel) => { const s = getComputedStyle(document.querySelector(sel));"
            " return [s.backgroundColor, s.borderTopColor, s.color]; }", selector)

    page.route("**/admin/api/models", lambda route: fulfil_json(route, ROWS))
    page.route("**/admin/api/fleet-placement", lambda route: fulfil_json(route, {"hosts": []}))
    for theme in ("light", "dark"):
        page.add_init_script(f"{_SHOW_ALL} localStorage.setItem('llmhub.theme', '{theme}');")
        for viewport in (PHONE, DESKTOP):
            page.set_viewport_size(viewport)
            page.goto(admin_url, wait_until="load")
            assert page.evaluate("document.documentElement.dataset.theme") == theme
            page.click("#tabModels")
            page.wait_for_selector("#modelsList .model-row", state="visible", timeout=10000)
            where = f"{theme} {viewport['width']}px"

            stop_sel = '#modelsList .model-row[data-id="running_row"] .action-row-verb[data-act="stop"]'
            start_sel = '#modelsList .model-row[data-id="stopped_row"] .action-row-verb[data-act="start"]'
            for label, sel in (("Stop", stop_sel), ("Start", start_sel)):
                bg, border_w, border_style, shadow = read(sel)
                assert bg == "rgba(0, 0, 0, 0)", f"{label} row verb is filled ({where}): {bg}"
                assert border_w == "0px" or border_style == "none", f"{label} row verb is outlined ({where})"
                assert shadow == "none", f"{label} row verb has a shadow ({where})"
            assert not page.evaluate(
                "() => !!document.querySelector('#modelsList .action-row-verb--stop')"), (
                f"a row still emits the retired outlined-Stop class ({where})")

            service_stop = paint("#dockerStopBtn")
            if viewport["width"] >= 1000:
                page.locator('#modelsList .model-row[data-id="running_row"] .action-row-main').click()
                page.wait_for_selector('#modelsDetail .wide-detail-head [data-act="stop"]', state="visible", timeout=5000)
                assert paint('#modelsDetail .wide-detail-head [data-act="stop"]') == service_stop, f"detail Stop differs ({where})"


def _roles_reach_each_model_sheet_and_the_glance(page, admin_url, mark):
    page.route("**/admin/api/models", lambda route: fulfil_json(route, ROLE_MODELS))
    page.route("**/admin/api/roles", lambda route: fulfil_json(route, ROLES))
    page.route("**/admin/api/fleet-placement", lambda route: fulfil_json(route, ROLE_PLACEMENT))
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")

    expected = {"qwen35_4b": "Agentic Light", "whisper": "Audio · Transcribe (fallback)"}
    for model_id, role in expected.items():
        row = page.locator(f'#modelsList .model-row[data-id="{model_id}"] .action-row-main')
        row.wait_for(state="visible", timeout=10000)
        row.click()
        sheet = page.locator("#modelSheet[open]")
        sheet.wait_for(timeout=5000)
        page.wait_for_function(
            "(t) => (document.querySelector('#modelSheet[open]')?.innerText || '').includes(t)",
            arg=role, timeout=5000,
        )
        mark("models.model-sheet")
        page.click("#modelSheetCloseBtn")

    # The placement reaches the memory glance, one row per fleet host.
    page.wait_for_selector("#modelsMemoryCard", state="visible", timeout=5000)
    page.wait_for_function(
        "(document.getElementById('modelsMemoryList')?.innerText || '').includes('Tower')", timeout=10000)


def _open_phone_models(page, admin_url, models):
    """Open the Models tab once its list has settled.

    Opening the tab reads fleet-placement, and its landing repaints every row.
    Left to land on its own, that repaint can replace a row the test is already
    acting on, so the read is held until the rows are painted, then released,
    and the rows it replaces are waited out.
    """
    held = []
    page.add_init_script(_SHOW_ALL)
    page.route("**/admin/api/models", lambda route: fulfil_json(route, models))
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
    page.route("**/admin/api/fleet-placement", lambda route: fulfil_json(route, {"hosts": []}))
    for route in held:
        fulfil_json(route, {"hosts": []})
    page.wait_for_function("!window.__preRepaintRow.isConnected", timeout=10000)


def _the_menu_stays_reachable_at_both_ends_of_the_list(page, admin_url, mark):
    _open_phone_models(page, admin_url, DEMO_ROWS)
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

    # The first row has room below, so its menu stays below, at the fleet menu's size.
    page.keyboard.press("Escape")
    page.wait_for_selector("body > .row-menu", state="hidden", timeout=5000)
    page.evaluate("window.scrollTo(0, 0)")
    page.locator("#modelsList .model-row").first.locator(".row-kebab").click()
    page.wait_for_selector("body > .row-menu .row-menu-item", state="visible", timeout=5000)
    page.wait_for_timeout(150)
    g = page.evaluate(_GEOMETRY)
    assert g["list"]["top"] >= g["toggle"]["bottom"] - 1, f"room below: stay below: {g}"
    assert g["list"]["bottom"] <= g["nav"]["top"], g
    assert g["fontPx"] == 16, f"menu text should be body size: {g}"
    assert g["itemMinPx"] == 44 and g["glyphPx"] == 16, g
    mark("models.row-menu-phone")


def _an_open_menu_follows_its_row_through_a_repoll(page, admin_url):
    """The poll rebuilds every row; the open menu follows its row to the new
    kebab instead of closing or parking in a corner."""
    live = json.loads(json.dumps(DEMO_ROWS))
    _open_phone_models(page, admin_url, live)
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


def test_models_list(browser, admin_url, browser_name):
    with fresh_page(browser) as page:
        _running_all_follows_the_filter_and_persists(page, admin_url)
    with fresh_page(browser, viewport=PHONE) as page:
        _the_tab_opens_on_the_glance_and_a_row_pings_from_its_menu(
            page, admin_url, browser_name, lambda name: surface(page, name))
    with fresh_page(browser) as page:
        _a_row_verb_is_a_named_glyph_and_its_menu_acts(page, admin_url)
    with fresh_page(browser) as page:
        _row_verbs_are_unpainted_at_rest(page, admin_url)
    with fresh_page(browser, viewport=PHONE) as page:
        _roles_reach_each_model_sheet_and_the_glance(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser, viewport=PHONE, has_touch=True) as page:
        _the_menu_stays_reachable_at_both_ends_of_the_list(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser, viewport=PHONE, has_touch=True) as page:
        _an_open_menu_follows_its_row_through_a_repoll(page, admin_url)
