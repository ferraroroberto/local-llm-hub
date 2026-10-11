"""A row's main start/stop action carries a word, not a bare glyph (#647, J-01).

The design review's judgment pass flags list rows whose primary action is an
icon-only square or triangle that reads like a checkbox: the power control on
every Models row and the Start/Stop pair on the Hub's Docker, Langfuse and
AgentsView service rows. Both now show the verb beside the glyph.

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


def test_model_row_power_control_says_start_or_stop(page, admin_url):
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

    for row_id, word in (("running_row", "Stop"), ("stopped_row", "Start")):
        act = word.lower()
        btn = page.locator(f'#modelsList .model-row[data-id="{row_id}"] .row-verb[data-act="{act}"]')
        assert btn.count() == 1, f"{row_id} should carry exactly one power control"
        assert (btn.inner_text() or "").strip() == word, f"{row_id}: {btn.inner_text()!r}"

    # Start, then the memory glance is read once more after the action settles.
    for _ in range(50):  # the tab-open read lands first
        if fleet_reads:
            break
        page.wait_for_timeout(100)
    reads_before = len(fleet_reads)
    page.locator('#modelsList .model-row[data-id="stopped_row"] .row-verb[data-act="start"]').click()
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
    adopted.locator(".row-menu-toggle").click()
    items = adopted.locator(".row-menu-item")
    assert items.last.get_attribute("data-act") == "force-stop", "Force stop is the menu's last item"
    assert "danger" in (items.last.get_attribute("class") or "")
    items.last.click()
    page.wait_for_selector("#machinesConfirmDialog[open]", timeout=5000)
    assert "Force stop" in page.locator("#machinesConfirmTitle").inner_text()
    assert not dialogs, "Force stop must not use the browser's confirm()"
    page.click("#machinesConfirmCloseBtn")

    # Model log opens the backend's log tail.
    running = page.locator('#modelsList .model-row[data-id="running_row"]')
    running.locator(".row-menu-toggle").click()
    running.locator('.row-menu-item[data-act="log"]').click()
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
