"""A row's main start/stop action carries a word, not a bare glyph (#647, J-01).

The design review's judgment pass flags list rows whose primary action is an
icon-only square or triangle that reads like a checkbox: the power control on
every Models row and the Start/Stop pair on the Hub's Docker, Langfuse and
AgentsView service rows. Both now show the verb beside the glyph.
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
    ],
}


def test_model_row_power_control_says_start_or_stop(page, admin_url):
    page.add_init_script("localStorage.setItem('llmhub.models.activeOnly', 'false');")
    page.route(
        "**/admin/api/models",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(FAKE_MODELS)
        ),
    )
    page.goto(admin_url, wait_until="load")
    page.click("#tabModels")
    page.wait_for_selector("#modelsList .app-item", state="visible", timeout=10000)

    for row_id, word in (("running_row", "Stop"), ("stopped_row", "Start")):
        act = word.lower()
        btn = page.locator(
            f'#modelsList .app-item[data-id="{row_id}"] .app-icons button[data-act="{act}"]'
        )
        assert btn.count() == 1, f"{row_id} should carry exactly one power control"
        assert (btn.inner_text() or "").strip() == word, f"{row_id}: {btn.inner_text()!r}"


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
