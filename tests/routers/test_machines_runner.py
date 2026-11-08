"""The Actions runner on a Machines card: state, verbs and the API.

Everything runs against the fake SSH and GitHub layers in
``tests/_runner_fakes.py``, so no real machine, repo or runner is ever touched
and nothing from the developer's overlay is read.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from src import server as server_mod
from src.fleet import actions_runner as ar
from src.fleet import machine_console as mc
from src.host_profile import HostProfile, get_host
from tests._runner_fakes import CONFIG, OVERLAY, USER, install_fakes


@pytest.fixture
def fleet(write_config, monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "hubbox")
    write_config(CONFIG, machines=OVERLAY)
    return install_fakes(monkeypatch)


def _client() -> TestClient:
    return TestClient(server_mod.app)


# ----------------------------------------------------------------- the config

def test_overlay_carries_the_runner_block_and_a_plain_host_has_none(fleet):
    assert get_host("mini").actions_runner == {"workflow": "nightly.yml"}
    assert get_host("hubbox").actions_runner == {}  # an empty block still enables it
    assert get_host("plain").actions_runner is None


def test_card_flags_a_runner_host_but_never_the_hub_box(fleet):
    mini, hubbox, plain = (get_host(h) for h in ("mini", "hubbox", "plain"))
    assert mc._card_base(mini, is_host=False)["runner"] is True
    assert mc._card_base(plain, is_host=False)["runner"] is False
    assert mc._card_base(hubbox, is_host=True)["runner"] is False


@pytest.mark.parametrize("block", [
    {"dir": "../etc"}, {"dir": "a b"}, {"dir": "x; reboot"}, {"repo": "no-slash"},
    {"name": "a b"}, {"workflow": "../x.yml"}, "not-a-mapping",
])
def test_an_unusable_block_is_reported_not_run(block):
    host = dataclasses.replace(HostProfile(id="x", platform="darwin", enabled=[]), actions_runner=block)
    spec = ar.spec_for(host)
    assert spec is not None and spec.problem


# ----------------------------------------------------------------- the state

def test_online_idle_reads_both_sources_and_the_last_run(fleet):
    resp = _client().get("/admin/api/machines/mini/runner")
    assert resp.status_code == 200
    body = resp.json()
    assert body["headline"] == "online"
    assert body["service"]["state"] == "running"
    assert body["github"] == {"state": "online", "busy": False, "reason": ""}
    assert body["last_run"]["state"] == "passed"
    assert body["boot"]["state"] == "ok"
    assert body["actions"] == {"start": False, "stop": True, "install": False}


def test_the_repo_and_name_come_from_the_runner_file_not_the_config(fleet):
    mac, gh = fleet
    _client().get("/admin/api/machines/mini/runner")
    assert any(p.startswith(f"repos/example-owner/example-repo/actions/runners") for p in gh.paths)
    assert ar.spec_for(get_host("mini")).repo is None


def test_a_failed_last_run_is_reported_as_failed(fleet):
    mac, gh = fleet
    gh.run_conclusion = "failure"
    assert _client().get("/admin/api/machines/mini/runner").json()["last_run"]["state"] == "failed"


def test_stopped_service_with_github_offline_is_stopped(fleet):
    mac, gh = fleet
    mac.service, gh.status = "stopped", "offline"
    body = _client().get("/admin/api/machines/mini/runner").json()
    assert body["headline"] == "stopped"
    assert body["actions"] == {"start": True, "stop": False, "install": False}


def test_service_running_but_github_offline_is_a_disagreement(fleet):
    mac, gh = fleet
    gh.status = "offline"
    assert _client().get("/admin/api/machines/mini/runner").json()["headline"] == "mismatch"


def test_a_loaded_but_dead_service_is_not_read_as_running(fleet):
    mac, gh = fleet
    mac.service, gh.status = "loaded", "offline"
    body = _client().get("/admin/api/machines/mini/runner").json()
    assert body["service"]["state"] == "loaded"
    assert "last exit 1" in body["service"]["reason"]
    assert body["actions"]["start"] is True


def test_not_installed_offers_only_install(fleet):
    mac, gh = fleet
    mac.service, gh.status = "not_installed", "offline"
    body = _client().get("/admin/api/machines/mini/runner").json()
    assert body["headline"] == "not_installed"
    assert body["actions"] == {"start": False, "stop": False, "install": True}


def test_an_unreachable_machine_is_unknown_never_fine(fleet):
    mac, gh = fleet
    mac.reachable = False
    body = _client().get("/admin/api/machines/mini/runner").json()
    assert body["service"]["state"] == "unknown"
    assert body["headline"] == "unknown"
    assert body["boot"] is None
    assert body["github"]["state"] == "unknown"  # no .runner file to name the runner
    assert not any(body["actions"].values())


def test_github_unreadable_is_unknown_and_stop_is_refused(fleet):
    mac, gh = fleet
    gh.error = "gh is not logged in."
    body = _client().get("/admin/api/machines/mini/runner").json()
    assert body["github"]["state"] == "unknown"
    assert body["github"]["reason"] == "gh is not logged in."
    assert body["last_run"]["state"] == "unknown"
    assert body["headline"] == "unknown"
    assert body["actions"]["stop"] is False
    assert "cannot be confirmed idle" in body["blocked"]["stop"]


def test_a_missing_runner_folder_is_unknown(fleet):
    mac, gh = fleet
    mac.dir_missing = True
    body = _client().get("/admin/api/machines/mini/runner").json()
    assert body["service"] == {"state": "unknown", "reason": "The runner folder was not found."}


def test_a_runner_github_does_not_list_is_not_registered(fleet):
    mac, gh = fleet
    mac.service, gh.registered = "stopped", False
    body = _client().get("/admin/api/machines/mini/runner").json()
    assert body["github"]["state"] == "not_registered"
    assert body["headline"] == "stopped"


# --------------------------------------------------------------- boot facts

def test_boot_without_auto_login_names_the_owner_step(fleet):
    mac, gh = fleet
    mac.autologin = None
    boot = _client().get("/admin/api/machines/mini/runner").json()["boot"]
    assert boot["state"] == "owner_step" and boot["autologin"] is False
    assert "automatic login" in boot["owner_steps"][0]


def test_auto_login_for_another_user_is_not_the_runner_user(fleet):
    mac, gh = fleet
    mac.autologin = "someone-else"
    assert _client().get("/admin/api/machines/mini/runner").json()["boot"]["autologin"] is False


def test_filevault_on_blocks_auto_login_and_says_so(fleet):
    mac, gh = fleet
    mac.filevault = "On"
    boot = _client().get("/admin/api/machines/mini/runner").json()["boot"]
    assert boot["state"] == "owner_step"
    assert "FileVault" in boot["owner_steps"][0]


def test_sleep_enabled_is_an_owner_step(fleet):
    mac, gh = fleet
    mac.sleep = 10
    boot = _client().get("/admin/api/machines/mini/runner").json()["boot"]
    assert boot["sleep_disabled"] is False and boot["state"] == "owner_step"


# --------------------------------------------------------------------- verbs

def test_stop_runs_over_ssh_then_reports_the_reread(fleet):
    mac, gh = fleet
    resp = _client().post("/admin/api/machines/mini/runner/stop")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["outcome"] == "done"
    assert body["runner"]["service"]["state"] == "stopped"
    assert body["runner"]["github"]["state"] == "offline"
    assert "GitHub shows it offline" in body["message"]
    assert any("./svc.sh stop" in c for c in mac.commands)


def test_start_after_stop_comes_back_online(fleet):
    mac, gh = fleet
    mac.service, gh.status = "stopped", "offline"
    body = _client().post("/admin/api/machines/mini/runner/start").json()
    assert body["ok"] is True
    assert body["runner"]["headline"] == "online"


def test_github_lagging_behind_is_reported_as_service_only(fleet):
    mac, gh = fleet
    mac.service, gh.status = "stopped", "offline"
    gh.reads_until_flip = 10_000
    body = _client().post("/admin/api/machines/mini/runner/start").json()
    assert body["ok"] is False and body["outcome"] == "service_only"
    assert "GitHub still shows offline" in body["message"]


def test_stop_is_refused_while_a_job_runs_and_nothing_is_sent(fleet):
    mac, gh = fleet
    gh.busy = True
    resp = _client().post("/admin/api/machines/mini/runner/stop")
    assert resp.status_code == 409
    assert "job is running" in resp.json()["detail"]
    assert not any("svc.sh stop" in c for c in mac.commands)


def test_stop_is_refused_when_github_cannot_be_read(fleet):
    mac, gh = fleet
    gh.error = "GitHub did not answer."
    assert _client().post("/admin/api/machines/mini/runner/stop").status_code == 409
    assert not any("svc.sh stop" in c for c in mac.commands)


def test_start_on_a_running_service_is_refused(fleet):
    resp = _client().post("/admin/api/machines/mini/runner/start")
    assert resp.status_code == 409


def test_install_installs_and_starts_in_one_command(fleet):
    mac, gh = fleet
    mac.service, gh.status = "not_installed", "offline"
    body = _client().post("/admin/api/machines/mini/runner/install").json()
    assert body["ok"] is True and body["runner"]["service"]["state"] == "running"
    assert any("./svc.sh install && ./svc.sh start" in c for c in mac.commands)


def test_a_failed_command_is_reported_as_failed(fleet, monkeypatch):
    mac, gh = fleet
    mac.service, gh.status = "stopped", "offline"
    mac.on_verb = lambda verb: None  # the service never changes
    body = _client().post("/admin/api/machines/mini/runner/start").json()
    assert body["ok"] is False and body["outcome"] == "failed"
    assert "service reads stopped" in body["message"]


def test_an_unknown_verb_and_unconfigured_machines_are_refused(fleet):
    client = _client()
    assert client.post("/admin/api/machines/mini/runner/explode").status_code == 404
    assert client.get("/admin/api/machines/plain/runner").status_code == 404
    assert client.get("/admin/api/machines/nowhere/runner").status_code == 404
    # The hub's own machine is never managed from here, even with a block.
    assert client.get("/admin/api/machines/hubbox/runner").status_code == 400
    assert client.post("/admin/api/machines/hubbox/runner/stop").status_code == 400


def test_responses_carry_no_address_login_or_raw_output(fleet):
    mac, gh = fleet
    mac.reachable = False
    text = _client().get("/admin/api/machines/mini/runner").text
    assert "192.0.2" not in text and USER not in text
    gh.error = "GitHub refused the request (HTTP 403)."
    mac.reachable = True
    text = _client().get("/admin/api/machines/mini/runner").text
    assert "192.0.2" not in text and USER not in text
