"""Dormant machines (#765): an intentionally-off box raises no alert.

Covers the machine-local write path (Settings › Machines → machines.local.yaml
only, round-trip through a cache drop), the `/api/machines/dormant` router, and
the exclusion of a dormant host from every surface that reacts to a down peer:
the Machines card, peer probes, failover chains, placement, reconcile and the
sync fan-out. Everything runs against a temp config with invented hosts, so no
real machine, port or MAC is ever touched.
"""

from __future__ import annotations

import yaml
import pytest
from fastapi.testclient import TestClient

from src import config_write, fleet_reconcile, host_profile, machine_console as mc
from src import model_failover, model_registry, remote_stats
from src import server as server_mod
from tests._worker_loop import run_on_worker_thread as _run

CONFIG = {
    "hub": {"port": 8000},
    "hosts": {
        "alpha": {"platform": "linux", "default": True, "enabled": ["m1"]},
        "beta": {"platform": "linux", "enabled": ["m1"], "display_name": "Beta"},
        "gamma": {"platform": "linux", "enabled": ["m1"]},
    },
    "models": {
        "m1": {
            "display_name": "m1", "backend": "openai", "engine": "llama-server",
            "port": 8081, "model_path": "models/m1.gguf",
            "hosts": ["beta", "gamma", "alpha"], "startup": "eager",
        },
    },
}
OVERLAY_COMMENT = "# my private fleet\n"


@pytest.fixture
def cfg(write_config, monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "alpha")
    return write_config(CONFIG)


def _overlay(cfg_path):
    path = cfg_path.parent / "machines.local.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None


# ------------------------------------------------------------- overlay merge
def test_overlay_dormant_merges_onto_the_host_row(write_config):
    write_config(CONFIG, machines={"hosts": {"beta": {"dormant": True}}})
    assert host_profile.get_host("beta").dormant is True
    assert host_profile.get_host("gamma").dormant is False


def test_overlay_false_overrides_a_committed_true(write_config):
    committed = {**CONFIG, "hosts": {
        **CONFIG["hosts"], "beta": {**CONFIG["hosts"]["beta"], "dormant": True},
    }}
    write_config(committed, machines={"hosts": {"beta": {"dormant": False}}})
    assert host_profile.get_host("beta").dormant is False


# ----------------------------------------------------------------- the write
def test_toggle_round_trips_through_the_overlay_only(cfg):
    models_before = cfg.read_bytes()
    assert config_write.set_host_dormant("beta", True) is True
    assert _overlay(cfg) == {"hosts": {"beta": {"dormant": True}}}
    assert cfg.read_bytes() == models_before  # models.yaml is never touched

    # A fresh parse (what a restart does) still sees it.
    host_profile._CONFIG_CACHE.clear()
    assert host_profile.get_host("beta").dormant is True

    assert config_write.set_host_dormant("beta", True) is False  # idempotent
    assert config_write.set_host_dormant("beta", False) is True
    host_profile._CONFIG_CACHE.clear()
    assert host_profile.get_host("beta").dormant is False
    # Back to the committed value: the overlay records no deviation.
    assert _overlay(cfg) == {"hosts": {}}


def test_toggle_keeps_the_operators_comments_and_other_keys(cfg):
    path = cfg.parent / "machines.local.yaml"
    path.write_text(
        OVERLAY_COMMENT + "hosts:\n  beta:\n    address: 10.0.0.2  # lan\n", encoding="utf-8"
    )
    config_write.set_host_dormant("beta", True)
    text = path.read_text(encoding="utf-8")
    assert text.startswith(OVERLAY_COMMENT) and "# lan" in text
    assert yaml.safe_load(text)["hosts"]["beta"] == {"address": "10.0.0.2", "dormant": True}

    config_write.set_host_dormant("beta", False)
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["hosts"]["beta"] == {"address": "10.0.0.2"}


def test_switching_off_a_committed_dormant_writes_an_explicit_false(write_config, monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "alpha")
    committed = {**CONFIG, "hosts": {
        **CONFIG["hosts"], "beta": {**CONFIG["hosts"]["beta"], "dormant": True},
    }}
    path = write_config(committed)
    config_write.set_host_dormant("beta", False)
    assert _overlay(path)["hosts"]["beta"] == {"dormant": False}
    assert host_profile.get_host("beta").dormant is False


@pytest.mark.parametrize("host_id, value, status", [
    ("nope", True, 404),
    ("alpha", True, 400),      # the machine the hub runs on can't be dormant
    ("beta", "yes", 400),
    ("beta", None, 400),
])
def test_toggle_refusals(cfg, host_id, value, status):
    with pytest.raises(config_write.ConfigWriteError) as err:
        config_write.set_host_dormant(host_id, value)
    assert err.value.status == status
    assert _overlay(cfg) is None  # nothing was written


def test_an_unreadable_overlay_is_never_overwritten(cfg):
    path = cfg.parent / "machines.local.yaml"
    path.write_text("hosts: [unclosed\n", encoding="utf-8")
    with pytest.raises(config_write.ConfigWriteError) as err:
        config_write.set_host_dormant("beta", True)
    assert err.value.status == 409
    assert path.read_text(encoding="utf-8") == "hosts: [unclosed\n"


# ---------------------------------------------------------------- the router
def test_router_lists_peers_and_sets_the_flag(cfg):
    client = TestClient(server_mod.app)
    body = client.get("/admin/api/machines/dormant").json()
    assert [m["id"] for m in body["machines"]] == ["beta", "gamma"]  # not the local host
    assert all(m["dormant"] is False for m in body["machines"])
    assert body["machines"][0]["display_name"] == "Beta"

    r = client.put("/admin/api/machines/beta/dormant", json={"dormant": True})
    assert r.status_code == 200 and r.json()["dormant"] is True
    listed = client.get("/admin/api/machines/dormant").json()["machines"]
    assert {m["id"]: m["dormant"] for m in listed} == {"beta": True, "gamma": False}

    assert client.put("/admin/api/machines/alpha/dormant", json={"dormant": True}).status_code == 400
    assert client.put("/admin/api/machines/zzz/dormant", json={"dormant": True}).status_code == 404
    assert client.put("/admin/api/machines/beta/dormant", json={}).status_code == 400


# ------------------------------------------------- the Machines card: no alert
def test_dormant_card_is_neutral_and_never_sshd_or_hub_probed(write_config, monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "alpha")
    write_config(CONFIG, machines={"hosts": {"beta": {"dormant": True, "address": "10.0.0.2"}}})

    async def _no(*a, **k):
        raise AssertionError("a dormant machine must not be SSH'd or hub-probed")

    async def _off(host):
        return False

    monkeypatch.setattr(mc.remote_stats, "collect", _no)
    monkeypatch.setattr(mc.remote_stats, "peer_health", _no)
    monkeypatch.setattr(mc.remote_stats, "is_reachable", _off)
    card = _run(mc._probe_machine(host_profile.get_host("beta"), "alpha"))
    assert card["state"] == "dormant" and card["answering"] is False
    assert card["hub"] is None and card["detail"] == "Dormant"


def test_a_woken_dormant_machine_reads_as_answering_without_flipping_the_flag(write_config, monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "alpha")
    write_config(CONFIG, machines={"hosts": {"beta": {"dormant": True, "address": "10.0.0.2"}}})

    async def _on(host):
        return True

    monkeypatch.setattr(mc.remote_stats, "is_reachable", _on)
    card = _run(mc._probe_machine(host_profile.get_host("beta"), "alpha"))
    assert card["state"] == "dormant" and card["answering"] is True
    assert host_profile.get_host("beta").dormant is True  # the flag didn't change by itself


# -------------------------------------------------- never reached by anything
def test_peer_probes_skip_a_dormant_host(write_config, monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "alpha")
    write_config(CONFIG, machines={"hosts": {"beta": {"dormant": True, "address": "10.0.0.2"}}})

    async def _dial(owner):
        raise AssertionError("a dormant host must not be dialled")

    monkeypatch.setattr(remote_stats, "dial_address_async", _dial)
    health = _run(remote_stats.peer_health("beta"))
    assert health["reachable"] is False and health["dormant"] is True
    assert _run(remote_stats.remote_models(host_profile.get_host("beta"))) is None


def test_dormant_host_leaves_the_chain_placement_and_peer_set(write_config, monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "alpha")
    write_config(CONFIG, machines={"hosts": {"beta": {"dormant": True}}})
    m1 = model_registry.resolve("m1")
    assert model_failover.eligible_chain(m1) == ["gamma", "alpha"]
    assert model_registry.preferred_owner(m1) == "gamma"  # beta is passed over
    assert "beta" not in model_registry.hub_peer_ids()
    assert "gamma" in model_registry.hub_peer_ids()
    assert "beta" not in model_registry.desired_placement()


def test_reconcile_never_wakes_or_bootstraps_a_dormant_host(write_config, monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "alpha")
    write_config(CONFIG, machines={"hosts": {"beta": {"dormant": True, "mac": "aa:bb:cc:dd:ee:ff"}}})
    # An unowned model is "local everywhere", so beta stays in the derived set.
    monkeypatch.setattr(model_registry, "desired_placement", lambda: {"beta": ["m1"]})

    async def _no(*a, **k):
        raise AssertionError("reconcile reached a dormant host")

    monkeypatch.setattr(fleet_reconcile, "_reconcile_host", _no)
    results = _run(fleet_reconcile.reconcile_once())
    assert results == {"beta": {"dormant": True, "reachable": None}}


def test_a_dormant_chain_head_is_not_flagged_as_a_failover(write_config, monkeypatch):
    from app_web.routers import models as models_router

    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "alpha")
    write_config(CONFIG, machines={"hosts": {"beta": {"dormant": True}}})
    m1 = model_registry.resolve("m1")
    row = {}
    models_router._add_failover_fields(row, m1, "gamma")  # served by the next member
    assert row == {"preferred_host": "gamma", "failover": False}
