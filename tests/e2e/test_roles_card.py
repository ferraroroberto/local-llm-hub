"""Model decisions left Hub for Models (issue #373; moved by #737, removed by #738).

Route-intercepts every backing endpoint so the render is deterministic and no
real peer probe (fleet-placement's per-host TCP liveness check) ever runs —
same discipline as ``test_fleet_placement_tab.py``. The API contract itself
has its own unit tests in ``tests/test_roles_router.py``.

What this locks in:
  * Hub carries no Model decisions card and fetches neither roles nor
    placement (the old card was folded and lazy; now there is nothing to load);
  * the roles reach each model's sheet on Models, a fallback marked as one;
  * the placement reaches the Models memory glance, one row per fleet host.
"""

from __future__ import annotations

import json

from tests.e2e._surface import surface

FAKE_ROLES = {
    "roles": {
        "agentic_light": {
            "model_id": "qwen35_4b", "display_name": "Qwen3.5 4B",
            "notes": "fast lane", "fallback": [],
        },
        "audio.transcribe": {
            "model_id": "parakeet", "display_name": "Parakeet",
            "notes": None, "fallback": ["whisper"],
        },
    },
}

FAKE_PLACEMENT = {
    "placement": {"tower": ["whisper"]},
    "hosts": [
        {
            "id": "tower", "display_name": "Tower", "icon": "monitor",
            "local": True, "reachable": True, "can_ssh": False, "runs_hub": True,
            "eligible": [{"id": "whisper", "display_name": "Whisper Turbo"}],
            "placed": ["whisper"], "running": ["whisper"],
        },
    ],
}

# Two running models, so the default Running scope lists both.
FAKE_MODELS = {
    "models": [
        {
            "id": model_id, "display_name": name, "backend": "llama", "port": port,
            "aliases": [], "controllable": True, "ownership": "ours", "pid": 4242,
            "reachable": True, "host": "tower",
        }
        for model_id, name, port in (("qwen35_4b", "Qwen3.5 4B", 8082), ("whisper", "Whisper Turbo", 8090))
    ],
}


def _fulfil(body):
    return lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def test_hub_has_no_roles_card_and_fetches_neither(page, admin_url):
    calls = []

    def counted(body):
        def handler(route):
            calls.append(route.request.url)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        return handler

    page.route("**/admin/api/roles", counted(FAKE_ROLES))
    page.route("**/admin/api/fleet-placement", counted(FAKE_PLACEMENT))
    page.goto(admin_url, wait_until="load")
    page.wait_for_selector("#hubGlance", state="visible", timeout=5000)
    page.wait_for_timeout(300)  # give any accidental eager fetch a chance to fire
    assert page.locator("#rolesCard").count() == 0
    assert calls == [], f"Hub fetched {calls}"


def test_roles_reach_the_model_sheet_on_models(page, admin_url):
    page.set_viewport_size({"width": 390, "height": 844})
    page.route("**/admin/api/models", _fulfil(FAKE_MODELS))
    page.route("**/admin/api/roles", _fulfil(FAKE_ROLES))
    page.route("**/admin/api/fleet-placement", _fulfil(FAKE_PLACEMENT))
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
        surface(page, "models.model-sheet")
        page.click("#modelSheetCloseBtn")


def test_placement_reaches_the_models_memory_glance(page, admin_url):
    page.route("**/admin/api/roles", _fulfil(FAKE_ROLES))
    page.route("**/admin/api/fleet-placement", _fulfil(FAKE_PLACEMENT))
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#modelsMemoryCard", state="visible", timeout=5000)
    page.wait_for_function(
        "(document.getElementById('modelsMemoryList')?.innerText || '').includes('Tower')",
        timeout=10000,
    )
