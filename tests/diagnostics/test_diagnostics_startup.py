"""The hub's startup orphan sweep against a run started after the hub booted.

``_init_diagnostics`` runs as a background task, so under load it can reach
``close_orphan_runs`` after a capture the hub itself started is already in the
store. The sweep is meant for runs a *previous* hub left open; closing a live
one finalises it as ``interrupted`` and the capture's own finish then fails
with ``IllegalRunTransition``. The test makes the late sweep deterministic by
creating the run first, rather than hoping the box is slow enough."""

from __future__ import annotations

import asyncio
import time

import pytest

from src import server_lifecycle
from src.diagnostics import settings as diag_settings
from src.diagnostics import store


@pytest.fixture()
def isolated_diagnostics(tmp_path):
    store.set_db_path(tmp_path / "diag.db")
    diag_settings.set_settings_path(tmp_path / "diagnostics_settings.json")
    yield
    store.set_db_path(None)
    diag_settings.set_settings_path(None)


def _new_run() -> str:
    return store.create_run(machine_id="pc-test", os_name="TestOS",
                            interval_s=15.0, duration_s=60.0)


def test_late_sweep_leaves_a_run_started_after_boot(isolated_diagnostics):
    boot_ts = time.time()
    run_id = _new_run()

    asyncio.run(server_lifecycle._init_diagnostics(boot_ts))

    assert store.get_run(run_id)["status"] == "running"
    store.finish_run(run_id)
    assert store.get_run(run_id)["status"] == "complete"


def test_sweep_still_closes_a_run_a_previous_hub_left_open(isolated_diagnostics):
    run_id = _new_run()
    boot_ts = store.get_run(run_id)["started_at"] + 1.0

    asyncio.run(server_lifecycle._init_diagnostics(boot_ts))

    assert store.get_run(run_id)["status"] == "interrupted"
