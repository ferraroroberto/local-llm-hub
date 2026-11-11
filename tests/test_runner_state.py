"""The Actions runner's state is derived once: headline, allowed verbs, convergence.

Pins the table the Machines card, the verb buttons and the post-verb re-read
all read, without SSH or GitHub: every pair of readings and what it means.
"""

from __future__ import annotations

import pytest

from src.fleet.runner_state import (
    VERBS,
    GithubState,
    Headline,
    RunState,
    ServiceState,
    allowed,
    converged,
    github_state,
    headline,
    run_state,
)


@pytest.mark.parametrize(("service", "github", "busy", "expected"), [
    ("running", "online", False, Headline.ONLINE),
    ("running", "online", True, Headline.BUSY),
    ("stopped", "offline", None, Headline.STOPPED),
    ("stopped", "not_registered", None, Headline.STOPPED),
    ("not_installed", "not_registered", None, Headline.NOT_INSTALLED),
    ("running", "offline", None, Headline.MISMATCH),
    ("loaded", "online", None, Headline.MISMATCH),
    ("stopped", "online", None, Headline.MISMATCH),
    ("unknown", "online", None, Headline.UNKNOWN),
    ("running", "unknown", None, Headline.UNKNOWN),
])
def test_headline_combines_both_readings(service, github, busy, expected):
    assert headline(service, github, busy) is expected


def test_an_unread_source_is_never_folded_into_a_fine_headline():
    for other in GithubState:
        assert headline(ServiceState.UNKNOWN, other, False) is Headline.UNKNOWN
    for other in ServiceState:
        assert headline(other, GithubState.UNKNOWN, False) is Headline.UNKNOWN


# state-machine-proof: actions_runner
def test_a_verb_the_readings_do_not_allow_is_refused_with_its_reason():
    can, why = allowed("running", "online", True)
    assert can == {"start": False, "stop": False, "install": False}
    assert "job is running" in why["stop"]

    can, why = allowed("running", "unknown", None)
    assert not can["stop"]
    assert "cannot be confirmed idle" in why["stop"]

    assert allowed("running", "online", False)[0] == {"start": False, "stop": True, "install": False}
    assert allowed("not_installed", "not_registered", None)[0] == {"start": False, "stop": False, "install": True}
    assert allowed("stopped", "offline", None)[0]["start"] is True
    assert allowed("loaded", "online", False)[0] == {"start": True, "stop": True, "install": False}
    assert allowed("unknown", "unknown", None)[0] == {v: False for v in VERBS}


# state-machine-proof: actions_runner
def test_an_unknown_state_name_is_rejected_not_guessed():
    with pytest.raises(ValueError):
        headline("sleeping", "online", False)
    with pytest.raises(ValueError):
        allowed("running", "maybe", False)


def test_convergence_matches_what_each_verb_was_meant_to_do():
    assert converged("stop", "stopped", "offline") == (True, True)
    assert converged("stop", "running", "offline") == (False, True)
    assert converged("start", "running", "online") == (True, True)
    assert converged("install", "running", "offline") == (True, False)


def test_github_words_are_translated_at_the_boundary():
    assert github_state(None) is GithubState.NOT_REGISTERED
    assert github_state({"status": "online"}) is GithubState.ONLINE
    assert github_state({"status": "offline"}) is GithubState.OFFLINE
    assert github_state({}) is GithubState.OFFLINE

    assert run_state({"status": "queued"}) is RunState.QUEUED
    assert run_state({"status": "in_progress"}) is RunState.RUNNING
    done = {"status": "completed"}
    assert run_state({**done, "conclusion": "success"}) is RunState.PASSED
    assert run_state({**done, "conclusion": "failure"}) is RunState.FAILED
    assert run_state({**done, "conclusion": "timed_out"}) is RunState.FAILED
    assert run_state({**done, "conclusion": "cancelled"}) is RunState.CANCELLED
    assert run_state({**done, "conclusion": "skipped"}) is RunState.UNKNOWN
