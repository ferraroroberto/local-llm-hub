"""End-to-end tests for the Fleet-placement grid (issue #354).

The grid is UI over the Step-2 desired-state API (#353). These tests pin the
GET/PATCH contract with route interception so the render is deterministic and
no real backend is ever started: the SPA is the unit under test here (the API
itself has its own unit tests in ``tests/test_fleet_placement_router.py``).

What they lock in:
  * per-host groups render, each with its status chip and a switch per model;
  * a placed+running model carries the "running" badge;
  * an **offline** host renders the deferred-apply note (not an error), and its
    switches stay live — desired placement is editable while a machine is off;
  * flipping a switch issues the expected ``PATCH /admin/api/fleet-placement``
    with the host's new model list.
"""

from __future__ import annotations

import json

# A fixed two-host fleet: the local tower (online) and an offline Mac Mini.
FAKE_PLACEMENT = {
    "placement": {"tower": ["whisper"], "mac-mini-m4": ["parakeet"]},
    "hosts": [
        {
            "id": "tower", "display_name": "Tower", "icon": "monitor",
            "local": True, "reachable": True, "can_ssh": False, "runs_hub": True,
            "eligible": [
                {"id": "whisper", "display_name": "Whisper Turbo"},
                {"id": "qwen35_4b", "display_name": "Qwen3.5 4B"},
            ],
            "placed": ["whisper"], "running": ["whisper"],
        },
        {
            "id": "mac-mini-m4", "display_name": "Mac Mini M4", "icon": "server",
            "local": False, "reachable": False, "can_ssh": True, "runs_hub": True,
            "eligible": [
                {"id": "parakeet", "display_name": "Parakeet"},
                {"id": "qwen", "display_name": "Qwen"},
            ],
            "placed": ["parakeet"], "running": [],
        },
        {
            # Managed-only satellite: powered on (TCP liveness), but runs no hub
            # and has no models — shown honestly with the "not placeable here"
            # note and no toggles, never silently dropped (the #354 follow-up).
            "id": "gaming", "display_name": "Gaming", "icon": "server",
            "local": False, "reachable": True, "can_ssh": True, "runs_hub": False,
            "eligible": [], "placed": [], "running": [],
        },
    ],
}


def _install_routes(page):
    """Serve a fixed GET payload and fulfill PATCH without any real side effect."""
    def handler(route):
        req = route.request
        if req.method == "PATCH":
            merged = dict(FAKE_PLACEMENT["placement"])
            merged.update(json.loads(req.post_data or "{}"))
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"ok": True, "placement": merged, "applied": {}}),
            )
            return
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(FAKE_PLACEMENT),
        )

    page.route("**/admin/api/fleet-placement", handler)


def _open_fleet_card(page, admin_url):
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#paneModels", state="visible", timeout=5000)
    # The card is folded by default — open it so the switches are interactive.
    page.eval_on_selector("#fleetPlacementCard", "el => { el.open = true; }")
    page.wait_for_selector("#fleetPlacementBody .fleet-host", state="visible", timeout=10000)


def test_fleet_placement_renders_host_groups(page, admin_url):
    _install_routes(page)
    _open_fleet_card(page, admin_url)

    groups = page.locator("#fleetPlacementBody .fleet-host")
    assert groups.count() == 3, "expected one group per fleet host"

    tower = page.locator(".fleet-host", has_text="Tower")
    assert "This machine" in tower.locator(".hub-live-status").inner_text()
    # Placed + running → the running badge; every eligible model has a switch.
    assert tower.locator(".startup-row", has_text="Whisper Turbo").locator(".badge.good").count() == 1
    assert tower.locator("button.toggle[role='switch']").count() == 2

    mac = page.locator(".fleet-host", has_text="Mac Mini M4")
    assert "Offline" in mac.locator(".hub-live-status").inner_text()

    # A managed-only satellite (runs no hub) still shows — online, but with the
    # "not placeable here" note and no switches — rather than silently vanishing.
    gaming = page.locator(".fleet-host", has_text="Gaming")
    assert "Online" in gaming.locator(".hub-live-status").inner_text()
    assert gaming.locator("button.toggle[role='switch']").count() == 0
    assert "not placeable" in gaming.locator(".fleet-host-note").inner_text().lower()


def test_offline_host_shows_deferred_note_not_error(page, admin_url):
    _install_routes(page)
    _open_fleet_card(page, admin_url)

    mac = page.locator(".fleet-host", has_text="Mac Mini M4")
    note = mac.locator(".fleet-host-note")
    assert note.count() == 1, "offline host should carry the deferred-apply note"
    assert "power" in note.inner_text().lower(), "note should explain it applies on power-up"

    # An offline host is a deferred state, never an error empty-state…
    assert page.locator("#fleetPlacementBody .empty-state").count() == 0
    # …and its placement stays editable while the machine is off.
    switches = mac.locator("button.toggle[role='switch']")
    assert switches.count() == 2
    assert switches.first.is_enabled()


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
            "vram_mb": 8192, "est_vram_mb": 16000, "capacity_warning": True,
        },
        {
            "id": "mac-mini-m4", "display_name": "Mac Mini M4", "icon": "server",
            "local": False, "reachable": True, "can_ssh": True, "runs_hub": True,
            "eligible": [{"id": "parakeet", "display_name": "Parakeet"}],
            "placed": ["parakeet"], "running": [],
            "vram_mb": None, "est_vram_mb": 99999, "capacity_warning": False,
        },
    ],
}


def test_capacity_warning_renders_only_on_overcommitted_host(page, admin_url):
    """The overcommitted tower shows the advisory VRAM warning; the ceiling-less
    Mac Mini never does, even with a large footprint (#375)."""
    def handler(route):
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(CAPACITY_PLACEMENT),
        )
    page.route("**/admin/api/fleet-placement", handler)
    _open_fleet_card(page, admin_url)

    tower = page.locator(".fleet-host", has_text="Tower")
    warn = tower.locator(".fleet-capacity-warn")
    assert warn.count() == 1, "overcommitted host should show the capacity warning"
    assert "over vram capacity" in warn.inner_text().lower()

    mac = page.locator(".fleet-host", has_text="Mac Mini M4")
    assert mac.locator(".fleet-capacity-warn").count() == 0, \
        "a host with no declared ceiling must never warn"


def test_toggle_issues_patch(page, admin_url):
    _install_routes(page)
    _open_fleet_card(page, admin_url)

    # Qwen3.5 4B on the tower starts un-placed — toggling it on must PATCH the
    # tower's list with the model appended (and the already-placed one kept).
    tower = page.locator(".fleet-host", has_text="Tower")
    row = tower.locator(".startup-row", has_text="Qwen3.5 4B")

    def is_patch(req):
        return req.url.endswith("/admin/api/fleet-placement") and req.method == "PATCH"

    with page.expect_request(is_patch) as req_info:
        row.locator("button.toggle[role='switch']").click()

    body = json.loads(req_info.value.post_data or "{}")
    assert "tower" in body, f"PATCH targeted the wrong host: {body}"
    assert "qwen35_4b" in body["tower"], f"placed model missing from PATCH: {body}"
    assert "whisper" in body["tower"], "PATCH dropped the already-placed model"
