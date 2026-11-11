"""A diagnostics run's status moves through one declared lifecycle."""

from __future__ import annotations

import pytest

from src.diagnostics import store
from src.diagnostics.run_state import (
    IllegalRunTransition,
    RunStatus,
    settle,
    transition,
)


@pytest.fixture()
def db(tmp_path):
    store.set_db_path(tmp_path / "diag.db")
    yield store
    store.set_db_path(None)


def test_settle_derives_how_a_capture_ended():
    assert settle(stopped=False, failed=False) is RunStatus.COMPLETE
    assert settle(stopped=True, failed=False) is RunStatus.STOPPED
    assert settle(stopped=False, failed=True) is RunStatus.FAILED
    assert settle(stopped=True, failed=True) is RunStatus.FAILED


def test_a_running_run_may_finish_or_be_interrupted():
    for end in ("complete", "stopped", "failed", "interrupted"):
        assert transition("running", end).value == end


# state-machine-proof: diagnostics_run
def test_a_status_that_cannot_follow_the_current_one_is_refused():
    for current, new in [
        ("complete", "failed"), ("complete", "running"), ("stopped", "complete"),
        ("failed", "complete"), ("interrupted", "complete"), ("pruned", "complete"),
        ("pruned", "running"),
    ]:
        with pytest.raises(IllegalRunTransition):
            transition(current, new)


# state-machine-proof: diagnostics_run
def test_finish_run_refuses_an_illegal_status_and_leaves_the_row(db):
    run_id = db.create_run(
        machine_id="m", os_name="linux", hostname="h", interval_s=1.0, duration_s=None,
    )
    db.finish_run(run_id, status=RunStatus.STOPPED)
    db.finish_run(run_id, status=RunStatus.STOPPED)  # idempotent
    with pytest.raises(IllegalRunTransition):
        db.finish_run(run_id, status=RunStatus.COMPLETE)
    assert db.get_run(run_id)["status"] == "stopped"


def test_finish_run_on_an_unknown_run_is_a_no_op(db):
    db.finish_run("no-such-run", status=RunStatus.COMPLETE)
