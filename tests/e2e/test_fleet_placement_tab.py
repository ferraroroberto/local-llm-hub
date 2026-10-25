"""End-to-end tests for the Models memory glance and each machine's summary
(issues #354, #430, #431; meters since #737).

The glance is one row per fleet host with a memory meter, over the read-only
desired-state API (#353); a row opens that machine's summary: the models
running now (live), the models it could load (chain members incl. on-demand),
and the capacity line. These tests pin the GET contract with route
interception so the render is deterministic and no real backend is ever
started: the SPA is the unit under test here (the API itself has its own unit
tests in ``tests/routers/test_fleet_placement_router.py``).

What they lock in:
  * #737: one meter row per host, live GPU first, the ``~`` estimate against
    the declared ceiling only as the fallback, and "not confirmed" (never an
    empty meter) when no figure exists; an over-capacity host's meter is in
    danger with the advisory warning in its summary;
  * the summary's Running list (live state, honest ``none``), Loadable list
    (eligible-but-not-running, incl. the cpu device hint), one model per line
    (#434), and the live-first Capacity line (#434/#436), at the same caption
    size as the model lines;
  * **zero interactive controls** inside the summary;
  * an **offline** host renders the deferred-apply note (not an error);
  * a host with nothing placeable shows an honest note instead of empty rows;
  * a backend the hub merely adopted from an external sibling on a
    mutex-shared port is labelled ``· external`` (#431).
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from tests.e2e._surface import surface

# A fixed fleet: the local tower (online), an offline Mac Mini, and a
# nothing-placeable machine.
FAKE_PLACEMENT = {
    "placement": {"tower": ["whisper"], "mac-mini-m4": ["parakeet"]},
    "hosts": [
        {
            "id": "tower", "display_name": "Tower", "icon": "monitor",
            "local": True, "reachable": True, "can_ssh": False, "runs_hub": True,
            "eligible": [
                {"id": "whisper", "display_name": "Whisper Turbo"},
                {"id": "qwen35_4b", "display_name": "Qwen3.5 4B"},
                {"id": "piper", "display_name": "Piper TTS", "device": "cpu"},
            ],
            "placed": ["whisper"], "running": ["whisper"], "external": [],
            "vram_mb": 16384, "est_vram_mb": 2048, "capacity_warning": False,
            "ram_mb": 131072,
            "ram": {"used_gb": 45.5, "total_gb": 128.0, "percent": 35.5},
            # Live GPU figures (#436) — the Machines tab's probe numbers.
            "gpu": {"used_mb": 1945.6, "total_mb": 8192.0},
        },
        {
            "id": "mac-mini-m4", "display_name": "Mac Mini M4", "icon": "server",
            "local": False, "reachable": False, "can_ssh": True, "runs_hub": True,
            "eligible": [
                {"id": "parakeet", "display_name": "Parakeet"},
                {"id": "qwen", "display_name": "Qwen"},
            ],
            "placed": ["parakeet"], "running": [], "external": [],
            "vram_mb": None, "est_vram_mb": 4096, "capacity_warning": False,
            # offline — no live figures (#434/#436): the ~estimate + declared
            # RAM total are the honest fallback.
            "ram_mb": 16384, "ram": None, "gpu": None,
        },
        {
            # Enrolled as a managed machine, serves no models — shown honestly
            # with a note and no rows, never silently dropped.
            "id": "openclaw", "display_name": "OpenClaw", "icon": "laptop",
            "local": False, "reachable": True, "can_ssh": True, "runs_hub": False,
            "eligible": [], "placed": [], "running": [], "external": [],
            "vram_mb": None, "est_vram_mb": 0, "capacity_warning": False,
            "ram_mb": None, "ram": None, "gpu": None,
        },
    ],
}


def _install_routes(page, payload=None):
    """Serve a fixed GET payload — the card is read-only (#430/#431)."""
    body = json.dumps(payload or FAKE_PLACEMENT)

    def handler(route):
        route.fulfill(status=200, content_type="application/json", body=body)

    page.route("**/admin/api/fleet-placement", handler)


def _open_glance(page, admin_url):
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#paneModels", state="visible", timeout=5000)
    page.wait_for_selector('#modelsMemoryList .memory-row[data-host-id="tower"]', state="visible", timeout=10000)


def _open_host(page, host_id):
    """A memory row opens that machine's summary (wide window: the detail pane)."""
    page.click(f'#modelsMemoryList .memory-row[data-host-id="{host_id}"] .action-row-main')
    body = page.locator("#modelsDetail .host-body")
    body.locator(".fleet-host").wait_for(state="visible", timeout=5000)
    return body


def _row_value(host_locator, label):
    """The right-hand value text of one summary line ("Running", …)."""
    return host_locator.locator(
        ".startup-row", has_text=label
    ).locator(".roles-row-value").inner_text()


def test_fleet_summary_renders_read_only_host_groups(page, admin_url):
    _install_routes(page)
    _open_glance(page, admin_url)

    rows = page.locator("#modelsMemoryList .memory-row")
    assert rows.count() == 3, "expected one meter row per fleet host"
    # Live GPU first (#436): 1945.6 of 8192 MB, no estimate marker.
    tower_row = page.locator('.memory-row[data-host-id="tower"]')
    assert "GPU 1.9 / 8.0 GB" in tower_row.inner_text()
    assert tower_row.locator(".meter-pct").inner_text() == "24%"
    # No figure at all reads "not confirmed", never an empty meter.
    claw_row = page.locator('.memory-row[data-host-id="openclaw"]')
    assert "not confirmed" in claw_row.locator(".memory-meter").inner_text()
    assert claw_row.locator(".meter-track").count() == 0
    surface(page, "models.memory-glance")

    tower = _open_host(page, "tower")
    assert "This machine" in tower.locator(".fleet-host-state").inner_text()
    # Running = live state; Loadable = eligible-but-not-running, with the
    # low-emphasis cpu device hint riding the CPU-resident row. One model
    # per line (#434) — .fleet-model-line, muted caption size.
    running_lines = tower.locator(".startup-row", has_text="Running").locator(".fleet-model-line")
    assert running_lines.count() == 1
    assert "Whisper Turbo" in running_lines.first.inner_text()
    assert "muted" in (running_lines.first.get_attribute("class") or "")
    assert "small" in (running_lines.first.get_attribute("class") or "")

    loadable_lines = tower.locator(".startup-row", has_text="Loadable").locator(".fleet-model-line")
    assert loadable_lines.count() == 2, "one line per loadable model"
    texts = [loadable_lines.nth(i).inner_text() for i in range(2)]
    assert any("Qwen3.5 4B" in t for t in texts)
    assert any("Piper TTS" in t and "cpu" in t for t in texts)

    # Capacity (#434/#436): live-first, no `~` anywhere on a live host.
    capacity = _row_value(tower, "Capacity")
    assert "GPU 1.9 / 8.0 GB" in capacity
    assert "RAM 45.5 / 128.0 GB" in capacity
    assert "~" not in capacity, "a live host must never show the estimate marker"

    # #431: a summary, not a control plane — no control in the summary.
    assert tower.locator("button").count() == 0
    surface(page, "models.host-summary")

    # A machine with nothing placeable still shows, with an honest note naming
    # the control that fills it (J-09, #647), rather than silently vanishing.
    claw = _open_host(page, "openclaw")
    assert claw.locator(".startup-row").count() == 0
    note = claw.locator(".fleet-host-note").inner_text().lower()
    assert "no models placeable" in note and "edit placement" in note
    surface(page, "models.host-nothing-placeable")


def test_offline_host_shows_deferred_note_not_error(page, admin_url):
    _install_routes(page)
    _open_glance(page, admin_url)

    # Offline, no live figures: the estimate against the declared RAM total.
    mac_row = page.locator('.memory-row[data-host-id="mac-mini-m4"]')
    assert "RAM ~4.0 / 16.0 GB" in mac_row.inner_text()
    assert mac_row.locator(".meter-pct").inner_text() == "~25%"

    mac = _open_host(page, "mac-mini-m4")
    note = mac.locator(".fleet-host-note")
    assert note.count() == 1, "offline host should carry the deferred-apply note"
    assert "power" in note.inner_text().lower(), "note should explain it applies on power-up"
    # A deferred state, never an error empty-state, with an honest empty Running line.
    assert mac.locator(".empty-state").count() == 0
    assert "none" in _row_value(mac, "Running").lower()
    assert "Parakeet" in _row_value(mac, "Loadable")

    # #434/#436: identical capacity shape on the ceiling-less unified-memory host.
    capacity = _row_value(mac, "Capacity")
    assert "GPU ~4.0 GB / 16.0 GB" in capacity
    assert "RAM 16 GB" in capacity
    assert "est" not in capacity


def test_capacity_value_matches_model_line_typography(page, admin_url):
    """#436: the Capacity value must render at the same font size as the block
    .fleet-model-line values: body-sm, never caption (TYPE-01, #810)."""
    _install_routes(page)
    _open_glance(page, admin_url)

    tower = _open_host(page, "tower")
    cap_value = tower.locator(".startup-row", has_text="Capacity").locator(".roles-row-value")
    model_line = tower.locator(".fleet-model-line").first
    font = "el => getComputedStyle(el).fontSize"
    assert cap_value.evaluate(font) == model_line.evaluate(font) == "14px"


def test_external_adopted_backend_labelled(page, admin_url):
    """A live backend served by a foreign adopted process carries the distinct
    ``· external`` marker — the summary must not claim the hub runs it (#431)."""
    payload = json.loads(json.dumps(FAKE_PLACEMENT))
    payload["hosts"][0]["external"] = ["whisper"]
    _install_routes(page, payload)
    _open_glance(page, admin_url)

    running = _row_value(_open_host(page, "tower"), "Running")
    assert "Whisper Turbo" in running and "external" in running


# A fleet where the tower overcommits its VRAM ceiling and the Mac Mini (no
# declared ceiling) does not — pins the advisory capacity warning (#375).
CAPACITY_PLACEMENT = {
    "placement": {"tower": ["gemma4_26b", "whisper"], "mac-mini-m4": ["parakeet"]},
    "hosts": [
        {
            "id": "tower", "display_name": "Tower", "icon": "monitor",
            "local": True, "reachable": True, "can_ssh": False, "runs_hub": True,
            "eligible": [
                {"id": "gemma4_26b", "display_name": "Gemma4 26B"},
                {"id": "whisper", "display_name": "Whisper Turbo"},
            ],
            "placed": ["gemma4_26b", "whisper"], "running": ["gemma4_26b", "whisper"],
            "external": [],
            "vram_mb": 8192, "est_vram_mb": 16000, "capacity_warning": True,
            "ram_mb": 131072,
        },
        {
            "id": "mac-mini-m4", "display_name": "Mac Mini M4", "icon": "server",
            "local": False, "reachable": True, "can_ssh": True, "runs_hub": True,
            "eligible": [{"id": "parakeet", "display_name": "Parakeet"}],
            "placed": ["parakeet"], "running": [], "external": [],
            "vram_mb": None, "est_vram_mb": 99999, "capacity_warning": False,
            "ram_mb": None,
        },
    ],
}


def test_capacity_warning_renders_only_on_overcommitted_host(page, admin_url):
    """The overcommitted tower's meter is in danger and its summary shows the
    advisory VRAM warning; the ceiling-less Mac Mini never does (#375)."""
    _install_routes(page, CAPACITY_PLACEMENT)
    _open_glance(page, admin_url)

    tower_row = page.locator('.memory-row[data-host-id="tower"]')
    assert "is-over" in (tower_row.get_attribute("class") or "")
    assert tower_row.locator('.memory-meter[data-level="high"]').count() == 1
    # The header names it once the models read lands, which can trail the
    # stubbed placement by about a second: wait, don't read at once (#746).
    expect(page.locator("#modelsHeadLine")).to_contain_text("over capacity", timeout=10000)
    warn = _open_host(page, "tower").locator(".fleet-capacity-warn")
    assert warn.count() == 1, "overcommitted host should show the capacity warning"
    assert "over vram capacity" in warn.inner_text().lower()

    mac_row = page.locator('.memory-row[data-host-id="mac-mini-m4"]')
    assert "is-over" not in (mac_row.get_attribute("class") or "")
    assert _open_host(page, "mac-mini-m4").locator(".fleet-capacity-warn").count() == 0, \
        "a host with no declared ceiling must never warn"
