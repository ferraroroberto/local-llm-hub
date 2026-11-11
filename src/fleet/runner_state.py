"""The GitHub Actions runner's state, derived in one place.

A fleet machine's runner is two facts that fail separately: the service on the
machine (``svc.sh status``) and GitHub's view of the runner. This module names
every state either source can report, translates GitHub's own words at the
boundary, and holds the three rules that combine them, so the Machines card,
the verb buttons and the post-verb re-read cannot disagree:

* :func:`headline` - the one state for the card face.
* :func:`allowed` - which verbs the readings permit (the machine's transition
  table: a verb outside it is refused with the reason given here).
* :func:`converged` - whether a re-read matches what a verb was meant to do.

A source that could not be read is ``unknown``, never folded into a fine state.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

VERBS = ("start", "stop", "install")


class ServiceState(str, Enum):
    """What ``svc.sh status`` says about the runner service."""

    RUNNING = "running"
    STOPPED = "stopped"
    LOADED = "loaded"  # registered with launchd but no process (crashed or exited)
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"


class GithubState(str, Enum):
    """GitHub's view of the runner."""

    ONLINE = "online"
    OFFLINE = "offline"
    NOT_REGISTERED = "not_registered"
    UNKNOWN = "unknown"


class RunState(str, Enum):
    """The latest result of the runner's workflow."""

    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RUNNING = "running"
    QUEUED = "queued"
    NONE = "none"
    UNKNOWN = "unknown"


class Headline(str, Enum):
    """The runner as one state, from both readings."""

    ONLINE = "online"
    BUSY = "busy"
    STOPPED = "stopped"
    NOT_INSTALLED = "not_installed"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


# GitHub's conclusions for a finished workflow run, in this machine's words.
_CONCLUSIONS = {
    "success": RunState.PASSED,
    "failure": RunState.FAILED,
    "cancelled": RunState.CANCELLED,
    "timed_out": RunState.FAILED,
}


def github_state(runner: Optional[Mapping[str, Any]]) -> GithubState:
    """GitHub's entry for the runner (``None`` when it lists none) as a state."""
    if runner is None:
        return GithubState.NOT_REGISTERED
    return GithubState.ONLINE if runner.get("status") == "online" else GithubState.OFFLINE


def run_state(run: Mapping[str, Any]) -> RunState:
    """One workflow run, as GitHub reports it, as a state."""
    if run.get("status") != "completed":
        return RunState.RUNNING if run.get("status") == "in_progress" else RunState.QUEUED
    return _CONCLUSIONS.get(run.get("conclusion"), RunState.UNKNOWN)


def headline(service: str, github: str, busy: Optional[bool]) -> Headline:
    """The one state for the card face, from both readings."""
    svc, gh = ServiceState(service), GithubState(github)
    if svc is ServiceState.UNKNOWN or gh is GithubState.UNKNOWN:
        return Headline.UNKNOWN
    if svc is ServiceState.RUNNING and gh is GithubState.ONLINE:
        return Headline.BUSY if busy else Headline.ONLINE
    if svc in (ServiceState.STOPPED, ServiceState.NOT_INSTALLED) and gh in (
        GithubState.OFFLINE, GithubState.NOT_REGISTERED,
    ):
        return Headline.NOT_INSTALLED if svc is ServiceState.NOT_INSTALLED else Headline.STOPPED
    return Headline.MISMATCH


def allowed(service: str, github: str, busy: Optional[bool]) -> Tuple[Dict[str, bool], Dict[str, str]]:
    """Which verbs the current readings allow, and why the others do not."""
    svc, gh = ServiceState(service), GithubState(github)
    can: Dict[str, bool] = {v: False for v in VERBS}
    why: Dict[str, str] = {}
    if svc in (ServiceState.STOPPED, ServiceState.LOADED):
        can["start"] = True
    if svc is ServiceState.NOT_INSTALLED:
        can["install"] = True
    if svc in (ServiceState.RUNNING, ServiceState.LOADED):
        if busy is True:
            why["stop"] = "A job is running. Stop it once the job has finished."
        elif gh is not GithubState.UNKNOWN:
            can["stop"] = True
        else:
            why["stop"] = "GitHub's view could not be read, so it cannot be confirmed idle."
    return can, why


def converged(verb: str, service: str, github: str) -> Tuple[bool, bool]:
    """``(service matches the verb, GitHub matches the verb)``."""
    if verb == "stop":
        return service == ServiceState.STOPPED, github == GithubState.OFFLINE
    return service == ServiceState.RUNNING, github == GithubState.ONLINE
