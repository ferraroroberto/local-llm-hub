"""The Settings dialog: display, health, startup, worker pools, machines and about.

The header gear opens Settings. One phase per section, each in its own browser
context with its backing endpoints stubbed so the render is deterministic and
nothing real is repaired, started or written:

* Display: Small / Default / Large text and the theme persist across a reload;
* Health & install: "Fix all" appears only when a check has a fix, and a fix
  that fails raises an error toast, never the green success one;
* Startup: a failed toggle reverts to the last saved value;
* Worker pools: a saved size goes to the server and is refused with its reason when out of range;
* Machines: a switch marks a peer dormant;
* About: the models.yaml version.
"""

from __future__ import annotations

import json

from tests.e2e._flow import PHONE, fresh_page, fulfil_json
from tests.e2e._models_rows import gemma_row
from tests.e2e._surface import surface

ROOT_PX = {"small": 15.0, "default": 16.0, "large": 18.0}


def _status(checks):
    worst = "ok"
    for c in checks:
        if c["status"] in ("missing", "error"):
            worst = c["status"]
    return {"checks": checks, "worst_status": worst}


ALL_OK = _status([{"id": "venv", "label": "Virtual environment", "status": "ok", "detail": ""}])
ONE_MISSING = _status([
    {"id": "venv", "label": "Virtual environment", "status": "ok", "detail": ""},
    {"id": "dotenv", "label": "Env file", "status": "missing", "detail": "no .env",
     "fix_id": "create_env", "fix_label": "Create"},
])

STARTUP_PROFILE = {"profile": {"docker": False}, "services": [{"id": "docker", "label": "Docker"}]}


def _root_px(page) -> float:
    return page.evaluate("parseFloat(getComputedStyle(document.documentElement).fontSize)")


def _pick_size(page, size: str) -> None:
    page.click("#paneHub .home-settings")
    page.click(f"#textSizeControl [data-textsize='{size}']")
    page.click("#settingsCloseBtn")


def _text_size_and_theme_persist(page, admin_url, mark):
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#paneHub .home-settings", state="visible", timeout=5000)

    assert _root_px(page) == ROOT_PX["default"]
    for size in ("small", "large", "default"):
        _pick_size(page, size)
        assert _root_px(page) == ROOT_PX[size], size

    _pick_size(page, "large")
    page.reload(wait_until="load")
    page.wait_for_selector("#paneHub .home-settings", state="visible", timeout=5000)
    assert _root_px(page) == ROOT_PX["large"]
    page.click("#paneHub .home-settings")
    pressed = page.locator('#textSizeControl .segmented-item[aria-pressed="true"]')
    assert pressed.count() == 1
    assert pressed.get_attribute("data-textsize") == "large"
    mark("settings.display")

    # Theme sits beside it: an explicit choice persists, and System hands the
    # page back to the device.
    page.click('#themeSeg [data-theme-pref="dark"]')
    page.click("#settingsCloseBtn")
    page.reload(wait_until="load")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.click("#paneHub .home-settings")
    assert page.locator('#themeSeg [data-theme-pref="dark"]').get_attribute("aria-pressed") == "true"
    page.click('#themeSeg [data-theme-pref="system"]')
    assert page.evaluate("localStorage.getItem('llmhub.theme')") is None
    device = page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
    assert page.evaluate("document.documentElement.dataset.theme") == ("dark" if device else "light")


def _open_health(page, admin_url, body):
    page.route("**/admin/api/install/status", lambda route: fulfil_json(route, body))
    # Healthy services, so the header line has room to name the check (it shows
    # the first two exceptions).
    page.route("**/admin/api/services/status*", lambda route: fulfil_json(route, {
        "launchable": True, "docker": {"running": True}, "langfuse": {"reachable": True},
        "agentsview": {"host": None}}))
    page.goto(admin_url, wait_until="load")
    # The battery runs when its row opens.
    page.click("#paneHub .home-settings")
    page.click('#settingsDialog .settings-link[data-settings="health"]')
    page.wait_for_selector("#installRows .install-row", state="attached", timeout=10000)


def _fix_all_is_hidden_when_nothing_needs_fixing(page, admin_url, mark):
    _open_health(page, admin_url, ALL_OK)
    assert page.locator("#installFixAllBtn").is_hidden()
    assert page.locator("#installRefreshBtn").is_visible()
    mark("settings.health-page")


def _fix_all_reports_a_failed_fix(page, admin_url, mark):
    """``POST /admin/api/install/fix-all`` always returns 200, recording each fix's
    outcome in ``ran``; a failed repair is not announced with the green toast."""
    page.route("**/admin/api/install/fix-all", lambda route: fulfil_json(route, {
        "ran": [{"fix_id": "create_env", "ok": False, "error": "boom"}]}))
    _open_health(page, admin_url, ONE_MISSING)
    assert page.locator("#installFixAllBtn").is_visible()
    # The failing check is the row's value and Hub's header exception.
    assert "1 check failing" in page.inner_text("#installSummary")
    assert "install check failing" in page.inner_text("#hubHeadLine")
    mark("settings.health-fix-all")

    page.click("#installFixAllBtn")
    page.wait_for_function("() => !document.getElementById('toast').hidden")
    toast = page.locator("#toast")
    assert "error" in (toast.get_attribute("class") or "")
    assert "create_env" in toast.inner_text()
    assert "boom" in toast.inner_text()
    # The wrench icon is part of the label and must survive the Running… swap.
    assert page.locator("#installFixAllBtn svg").count() == 1


def _failed_startup_toggle_reverts_to_the_saved_value(page, admin_url):
    """Toggle a switch on (PATCH succeeds) then off (PATCH fails): it lands back
    on ON, the last saved state, never on the value it rendered with at build."""
    calls = []

    def _patch(route):
        req = route.request
        if req.method != "PATCH":
            fulfil_json(route, STARTUP_PROFILE)
            return
        calls.append(json.loads(req.post_data))
        if len(calls) == 1:
            fulfil_json(route, {"ok": True, "profile": {"docker": True}})
        else:
            fulfil_json(route, {"detail": "boom"}, status=500)

    page.route("**/admin/api/startup-profile", _patch)
    page.goto(admin_url, wait_until="load")
    page.click("#paneHub .home-settings")
    page.click('#settingsDialog .settings-link[data-settings="startup"]')

    switch = page.locator("#startupList button[role='switch']").first
    assert switch.get_attribute("aria-checked") == "false"
    switch.click()
    page.wait_for_function(
        "() => document.querySelector(\"#startupList button[role='switch']\").getAttribute('aria-checked') === 'true'"
    )
    switch.click()
    page.wait_for_function("() => !document.querySelector(\"#startupList button[role='switch']\").disabled")
    assert switch.get_attribute("aria-checked") == "true"
    assert len(calls) == 2


POOL_SPECS = [
    ("hot-poll", "Hot poll", 8), ("heavy-read", "Heavy read", 4), ("action", "Action", 12),
    ("fan-out", "Fan-out", 8), ("background", "Background", 6), ("media", "Media", 8),
]


def _pools_body(sizes, running=None):
    running = running or {pid: default for pid, _, default in POOL_SPECS}
    return {
        "min": 1, "max": 64, "restart_pending": sizes != running,
        "pools": [{"id": pid, "label": label, "description": "what it runs", "default": default,
                   "size": sizes[pid], "running": running[pid]} for pid, label, default in POOL_SPECS],
    }


def _worker_pool_sizes_save_and_are_refused_out_of_range(page, admin_url, mark):
    defaults = {pid: default for pid, _, default in POOL_SPECS}
    puts = []

    def _pools(route):
        req = route.request
        if req.method != "PUT":
            return fulfil_json(route, _pools_body(defaults))
        sizes = json.loads(req.post_data)["sizes"]
        puts.append(sizes)
        if sizes["action"] > 64:
            return fulfil_json(route, {"detail": "action: size must be from 1 to 64, got 65"}, status=400)
        return fulfil_json(route, {"ok": True, **_pools_body({**defaults, **sizes}, running=defaults)})

    page.route("**/admin/api/worker-pools", _pools)
    page.goto(admin_url, wait_until="load")
    page.click("#paneHub .home-settings")
    assert page.inner_text("#poolsSummary") == "Defaults"
    page.click('#settingsDialog .settings-link[data-settings="pools"]')

    assert page.locator("#poolsList .pool-row").count() == 6
    assert page.locator("#poolSize-action").input_value() == "12"
    assert page.locator("#poolsPending").is_hidden()
    mark("settings.pools-page")

    page.fill("#poolSize-action", "65")
    page.click("#poolsSaveBtn")
    page.wait_for_function("() => !document.getElementById('toast').hidden")
    assert "error" in (page.locator("#toast").get_attribute("class") or "")
    assert "action: size must be from 1 to 64" in page.locator("#toast").inner_text()
    assert page.inner_text("#poolsSummary") == "Defaults"

    page.fill("#poolSize-action", "20")
    page.click("#poolsSaveBtn")
    page.wait_for_function("() => document.getElementById('poolsSummary').textContent === '1 changed'")
    assert puts[-1] == {**defaults, "action": 20}, puts
    assert page.locator("#poolsPending").is_visible()
    assert page.locator("#poolSize-action").input_value() == "20"


def _a_switch_marks_a_machine_dormant(page, admin_url, mark):
    puts = []
    rows = [
        {"id": "attic", "display_name": "Attic", "role": "peer", "dormant": False},
        {"id": "den", "display_name": "Den", "role": "peer", "dormant": False},
    ]

    def _dormant(route):
        req = route.request
        if req.method == "PUT":
            body = json.loads(req.post_data)
            puts.append((req.url.rsplit("/", 2)[-2], body))
            return fulfil_json(route, {"ok": True, "id": "attic", "dormant": body["dormant"], "changed": True})
        fulfil_json(route, {"machines": rows})

    page.route("**/admin/api/machines/dormant", _dormant)
    page.route("**/admin/api/machines/attic/dormant", _dormant)
    page.goto(admin_url, wait_until="load")
    page.click("#paneHub .home-settings")
    assert page.inner_text("#dormantSummary") == "None"
    page.click('#settingsDialog .settings-link[data-settings="machines"]')

    items = page.locator("#dormantList .startup-row")
    assert items.count() == 2 and "Attic" in items.first.inner_text()
    assert "Mark as intentionally off" in items.first.inner_text()

    switch = items.first.locator('button[role="switch"]')
    assert switch.get_attribute("aria-checked") == "false"
    switch.click()
    page.wait_for_function(
        "() => document.querySelector('#dormantList button[role=switch]').getAttribute('aria-checked') === 'true'"
    )
    assert puts == [("attic", {"dormant": True})], puts
    assert page.inner_text("#dormantSummary") == "1 of 2"
    mark("settings.machines-page")


def _about_carries_the_models_config_version(page, admin_url, mark):
    body = {"models": [gemma_row()], "config": {
        "sha": "abc1234", "write_enabled": True, "write_host": "tower",
        "fleet_hosts": [{"id": "tower", "vram_mb": 16384}]}}
    page.add_init_script("localStorage.setItem('llmhub.models.activeOnly', 'false');")
    page.route("**/admin/api/models**", lambda route: fulfil_json(route, body))
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#modelsList .model-row", state="visible", timeout=10000)
    page.click("#paneModels .home-settings")
    assert page.locator("#settingsDialog #modelsConfigSha").inner_text() == "abc1234"
    mark("settings.about")

    # The two rows that leave the app are settings-row buttons, each opening its
    # address in a tab of its own; window.open is recorded, not followed.
    page.evaluate("window.__opened = []; window.open = (...a) => { window.__opened.push(a); return null; }; true")
    for row_id in ("aboutFrontier", "aboutLangfuse"):
        row = page.locator(f"#settingsDialog button.settings-row#{row_id}")
        assert row.count() == 1, f"{row_id} is a button, not a link"
        row.click()
    opened = page.evaluate("window.__opened")
    assert opened[0][0].endswith("/admin/frontier") and opened[0][1:] == ["_blank", "noopener"], opened
    assert opened[1][0].startswith("http") and opened[1][1:] == ["_blank", "noopener"], opened


def test_settings_dialog(browser, admin_url):
    with fresh_page(browser, viewport=PHONE) as page:
        _text_size_and_theme_persist(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser) as page:
        _fix_all_is_hidden_when_nothing_needs_fixing(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser) as page:
        _fix_all_reports_a_failed_fix(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser) as page:
        _failed_startup_toggle_reverts_to_the_saved_value(page, admin_url)
    with fresh_page(browser) as page:
        _worker_pool_sizes_save_and_are_refused_out_of_range(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser, viewport=PHONE, has_touch=True) as page:
        _a_switch_marks_a_machine_dormant(page, admin_url, lambda name: surface(page, name))
    with fresh_page(browser) as page:
        _about_carries_the_models_config_version(page, admin_url, lambda name: surface(page, name))
