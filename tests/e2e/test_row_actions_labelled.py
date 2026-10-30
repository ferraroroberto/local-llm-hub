"""How a row's main start/stop action is named and drawn (#647, #780).

A list row's one visible verb is a plain, unpainted, glyph-only icon-button
(fleet-config#1420): its accessible name and tooltip say the verb and the row
(``Start <model>``), it keeps a 44px target, and it has no fill or border at
rest. The Start/Stop pair on the Hub's Docker, Langfuse and AgentsView service
sheet and the wide detail pane's head are not list rows and keep their word
beside the glyph (#647, J-01).

Since #737 the Models verb is the shared row's one visible verb, and the same
story pins what happens around it: the memory glance is read once more after
a Start settles (no poll), Force stop of an adopted process confirms in the
vendored dialog (never the browser's confirm) with no port or process id on
the row face, and Model log opens the backend's log tail.
"""

from __future__ import annotations

import json

FAKE_MODELS = {
    "models": [
        {
            "id": "running_row", "display_name": "running-row", "backend": "llama",
            "engine": "llama-server", "port": 8081, "url": None, "aliases": [],
            "controllable": True, "ownership": "ours", "pid": 4242,
            "reachable": True, "model_path": None, "host": "tower",
            "preferred_host": "tower", "failover": False,
        },
        {
            "id": "stopped_row", "display_name": "stopped-row", "backend": "llama",
            "engine": "llama-server", "port": 8082, "url": None, "aliases": [],
            "controllable": True, "ownership": "none", "pid": None,
            "reachable": False, "model_path": None, "host": "tower",
            "preferred_host": "tower", "failover": False,
        },
        {
            "id": "adopted_row", "display_name": "adopted-row", "backend": "llama",
            "engine": "llama-server", "port": 8083, "url": None, "aliases": [],
            "controllable": True, "ownership": "external", "pid": 5151,
            "reachable": True, "model_path": None, "host": "tower",
            "preferred_host": "tower", "failover": False,
        },
    ],
}


def _json(route, body):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def test_model_row_power_control_is_a_named_glyph(page, admin_url):
    page.add_init_script("localStorage.setItem('llmhub.models.activeOnly', 'false');")
    fleet_reads = []
    started = []
    page.route("**/admin/api/models", lambda route: _json(route, FAKE_MODELS))
    page.route("**/admin/api/fleet-placement", lambda route: (fleet_reads.append(1), _json(route, {"hosts": []}))[-1])
    page.route("**/admin/api/models/stopped_row/start", lambda route: (started.append(1), _json(route, {"ok": True}))[-1])
    page.route("**/admin/api/models/running_row/log*",
               lambda route: _json(route, {"id": "running_row", "lines": ["demo line one", "demo line two"]}))
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


def test_service_rows_start_stop_say_the_verb(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    page.wait_for_selector("#paneHub", state="attached")
    # The buttons are shown one at a time by service state; the markup of all six
    # is there regardless, so read the words off the DOM rather than the state.
    for service in ("docker", "langfuse", "agentsview"):
        for verb in ("Start", "Stop"):
            btn = page.locator(f"#{service}{verb}Btn")
            assert btn.count() == 1, f"missing #{service}{verb}Btn"
            assert (btn.text_content() or "").strip() == verb, f"{service} {verb} is icon-only"


def test_row_verbs_are_unpainted_at_rest_everywhere(page, admin_url):
    """No list-row verb is filled or outlined at rest (fleet-config#1420, #780).

    Models-row Start and Stop are the same plain icon-button, told apart by
    glyph and name alone, in both themes at phone and desktop width. The old
    outlined-Stop alias class is gone from the markup. The wide detail pane's
    head is not a list row: its Stop stays outlined, drawn the way the
    Services sheet draws its Stop (#763).
    """
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

    for theme in ("light", "dark"):
        page.add_init_script(
            "localStorage.setItem('llmhub.models.activeOnly', 'false');"
            f"localStorage.setItem('llmhub.theme', '{theme}');")
        page.route("**/admin/api/models", lambda route: _json(route, FAKE_MODELS))
        page.route("**/admin/api/fleet-placement", lambda route: _json(route, {"hosts": []}))
        for viewport in ({"width": 390, "height": 844}, {"width": 1280, "height": 900}):
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

            # The detail pane and Services draw Stop the same way.
            service_stop = paint("#dockerStopBtn")

            if viewport["width"] >= 1000:
                page.locator('#modelsList .model-row[data-id="running_row"] .action-row-main').click()
                page.wait_for_selector('#modelsDetail .wide-detail-head [data-act="stop"]', state="visible", timeout=5000)
                assert paint('#modelsDetail .wide-detail-head [data-act="stop"]') == service_stop, f"detail Stop differs ({where})"
