"""End-to-end tests for the Runner section of a machine's sheet (#980).

The browser drives the real SPA against the real ``actions_runner`` code; only
the two edges are fake. ``tests/_runner_fakes.py`` stands in for SSH to the
runner Mac and for ``gh api``, and a route handler serves the Runner endpoints
by calling ``actions_runner`` through them (the isolated e2e hub cannot spawn
``ssh`` or ``gh`` at all, its egress guard refuses). The machine status is
served from a fixed payload, as the other Machines tests do, so no real fleet
is probed.

Pinned here: the section shows the service and GitHub's view separately; Stop
and Start toast the outcome of the re-read, not of the request; Stop is refused
with its reason while a job runs; a source that cannot be read says "Not
confirmed" and offers no verb; an outstanding owner step is named on the card.
"""

from __future__ import annotations

import json
import re

import pytest

from src.fleet import actions_runner as ar
from tests._runner_fakes import CONFIG, OVERLAY, install_fakes
from tests._worker_loop import run_on_worker_thread
from tests.e2e._surface import surface

PHONE_VIEWPORT = {"width": 390, "height": 844}
WIDE_VIEWPORT = {"width": 1440, "height": 900}

FAKE_STATUS = {
    "machines": [
        {
            "id": "hubbox", "display_name": "Hub box", "icon": "monitor",
            "state": "self", "is_host": True, "runs_hub": True, "role": "hub",
            "actions": {}, "hub": None, "runner": False,
        },
        {
            "id": "mini", "display_name": "Mac Mini", "icon": "server",
            "state": "up", "is_host": False, "runs_hub": False, "role": "runner host",
            "uptime_seconds": 7200, "actions": {"wake": False}, "hub": None, "runner": True,
        },
    ]
}

_RUNNER_URL = re.compile(r".*/admin/api/machines/mini/runner(/[a-z]+)?(\?.*)?$")


@pytest.fixture()
def fakes(write_config, monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "hubbox")
    write_config(CONFIG, machines=OVERLAY)
    return install_fakes(monkeypatch)


def _serve(page):
    """Fixed machine status; the Runner endpoints answered by ``actions_runner``
    over the fakes."""
    page.route(
        "**/admin/api/machines/status",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(FAKE_STATUS)),
    )

    def runner(route):
        req = route.request
        try:
            if req.method == "GET":
                body = run_on_worker_thread(ar.status("mini", fresh="fresh=true" in req.url))
            else:
                verb = req.url.split("?")[0].rsplit("/", 1)[1]
                body = run_on_worker_thread(ar.run_verb("mini", verb))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        except ar.RunnerRefused as exc:
            route.fulfill(status=exc.status, content_type="application/json", body=json.dumps({"detail": exc.detail}))

    page.route(_RUNNER_URL, runner)


@pytest.fixture()
def phone(browser, admin_url, fakes):
    context = browser.new_context(viewport=PHONE_VIEWPORT, has_touch=True, is_mobile=False)
    page = context.new_page()
    page.set_default_timeout(15000)
    _serve(page)
    page.goto(admin_url, wait_until="load")
    page.click("#tabMachines")
    page.wait_for_selector(".machine-row", state="visible", timeout=15000)
    yield page
    context.close()


def _open_mini(page):
    page.locator('.machine-row[data-machine-id="mini"] .action-row-main').click()
    page.wait_for_selector("#machineSheet[open]", timeout=5000)
    section = page.locator("#machineSheetBody .machine-runner")
    section.wait_for(timeout=5000)
    return section


def _wait_headline(section, headline, selector="#machineSheetBody .machine-runner"):
    section.page.wait_for_function(
        "([sel, h]) => { const e = document.querySelector(sel); return e && e.dataset.runnerHeadline === h; }",
        arg=[selector, headline], timeout=10000,
    )


def _toast_text(page):
    toast = page.locator("#toast")
    toast.wait_for(state="visible", timeout=10000)
    return toast.inner_text()


def test_the_section_shows_service_and_github_separately(phone, fakes):
    section = _open_mini(phone)
    _wait_headline(section, "online")

    assert section.locator("[data-machine-runner-state]").inner_text() == "Online · idle"
    assert section.locator("[data-runner-service]").inner_text() == "Running"
    assert section.locator("[data-runner-github]").inner_text() == "Online · idle"
    assert section.locator("[data-runner-last-run]").inner_text().startswith("Passed")
    assert section.locator("[data-runner-boot]").inner_text() == "Auto-login on · sleep off"
    assert section.locator('[data-machine-action="runner-stop"]').is_enabled()
    assert section.locator('[data-machine-action="runner-start"]').is_disabled()
    # The section sits above the danger rows, which stay last.
    actions = phone.locator("#machineSheetBody [data-machine-action]").evaluate_all(
        "els => els.map(e => e.dataset.machineAction)")
    assert actions[-2:] == ["reboot", "shutdown"], actions
    surface(phone, "machines.runner-online")


def test_stop_then_start_toast_the_reread_outcome(phone, fakes):
    mac, gh = fakes
    section = _open_mini(phone)
    _wait_headline(section, "online")

    section.locator('[data-machine-action="runner-stop"]').click()
    assert "Runner stopped. GitHub shows it offline." in _toast_text(phone)
    _wait_headline(section, "stopped")
    assert section.locator("[data-runner-service]").inner_text() == "Stopped"
    assert section.locator("[data-runner-github]").inner_text() == "Offline"
    assert section.locator('[data-machine-action="runner-start"]').is_enabled()
    assert section.locator('[data-machine-action="runner-stop"]').is_disabled()
    assert any("./svc.sh stop" in c for c in mac.commands)

    section.locator('[data-machine-action="runner-start"]').click()
    _wait_headline(section, "online")
    assert "Runner started. GitHub shows it online." in phone.locator("#toast").inner_text()
    assert section.locator("[data-machine-runner-state]").inner_text() == "Online · idle"


def test_stop_is_refused_with_its_reason_while_a_job_runs(phone, fakes):
    mac, gh = fakes
    section = _open_mini(phone)
    _wait_headline(section, "online")

    gh.busy = True  # a job started after the sheet last read the runner
    section.locator('[data-machine-action="runner-stop"]').click()
    assert "A job is running" in _toast_text(phone)
    assert not any("svc.sh stop" in c for c in mac.commands), "nothing may be sent while busy"
    # The refusal re-reads: the section now says so and Stop is off.
    _wait_headline(section, "busy")
    assert section.locator("[data-machine-runner-state]").inner_text() == "Online · running a job"
    assert section.locator('[data-machine-action="runner-stop"]').is_disabled()
    assert "A job is running" in section.locator("[data-runner-blocked]").inner_text()


def test_unreadable_sources_say_not_confirmed_and_offer_no_verb(phone, fakes):
    mac, gh = fakes
    mac.reachable = False
    section = _open_mini(phone)
    _wait_headline(section, "unknown")

    assert section.locator("[data-machine-runner-state]").inner_text() == "Not confirmed"
    assert section.locator("[data-runner-service]").inner_text() == "Not confirmed"
    assert section.locator("[data-runner-github]").inner_text() == "Not confirmed"
    assert section.locator("[data-runner-boot]").inner_text() == "Not confirmed"
    assert "The machine did not answer over SSH." in section.inner_text()
    # With no SSH there is no .runner file to name the runner, so GitHub is not asked.
    assert "The runner's repo or name could not be read." in section.inner_text()
    for verb in ("start", "stop"):
        assert section.locator(f'[data-machine-action="runner-{verb}"]').is_disabled()
    surface(phone, "machines.runner-not-confirmed")


def test_an_outstanding_owner_step_is_named_on_the_card(phone, fakes):
    mac, gh = fakes
    mac.autologin = None
    section = _open_mini(phone)
    _wait_headline(section, "online")

    assert section.locator("[data-runner-boot]").inner_text() == "Needs a step at the Mac"
    step = section.locator("[data-runner-step]")
    assert "automatic login" in step.inner_text()
    assert step.get_attribute("data-tone") == "attention"


def test_install_is_offered_when_the_service_is_missing(phone, fakes):
    mac, gh = fakes
    mac.service, gh.status = "not_installed", "offline"
    section = _open_mini(phone)
    _wait_headline(section, "not_installed")

    assert section.locator('[data-machine-action="runner-start"]').count() == 0
    section.locator('[data-machine-action="runner-install"]').click()
    assert "Runner installed and started." in _toast_text(phone)
    _wait_headline(section, "online")


def test_a_wide_window_shows_the_same_section_in_the_detail_pane(browser, admin_url, fakes):
    context = browser.new_context(viewport=WIDE_VIEWPORT)
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        _serve(page)
        page.goto(admin_url, wait_until="load")
        page.click("#tabMachines")
        page.wait_for_selector(".machine-row", state="visible", timeout=15000)
        page.locator('.machine-row[data-machine-id="mini"] .action-row-main').click()
        section = page.locator("#machinesDetail .machine-runner")
        section.wait_for(timeout=5000)
        _wait_headline(section, "online", "#machinesDetail .machine-runner")
        assert section.locator("[data-runner-service]").inner_text() == "Running"
        assert page.locator("#machineSheet[open]").count() == 0
    finally:
        context.close()
