"""A diagnostics run's status, declared in one place.

A capture run is a row in ``runs``. Its ``status`` moves through a short
lifecycle, and every writer goes through this module so a status that cannot
follow the current one is refused instead of silently overwriting it:

* ``running`` - the capture is in progress.
* ``complete`` - it reached its deadline (or was a one-shot) and was judged.
* ``stopped`` - the owner stopped it, or the hub cancelled the task.
* ``failed`` - the capture task raised.
* ``interrupted`` - the hub died mid-capture; closed at the next startup.
* ``pruned`` - a finished run whose raw samples aged out.

:func:`settle` derives how a capture ended from what its task saw;
:func:`transition` is the only code that says which status may follow which.
"""

from __future__ import annotations

from enum import Enum


class RunStatus(str, Enum):
    """Values are what the ``runs.status`` column stores."""

    RUNNING = "running"
    COMPLETE = "complete"
    STOPPED = "stopped"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    PRUNED = "pruned"


FINISHED = frozenset({RunStatus.COMPLETE, RunStatus.STOPPED, RunStatus.FAILED})

# What may follow each status. A finished run may be finished again with the
# same status (finalize is idempotent) and any closed run may be pruned.
_NEXT = {
    RunStatus.RUNNING: FINISHED | {RunStatus.INTERRUPTED},
    RunStatus.COMPLETE: {RunStatus.COMPLETE, RunStatus.PRUNED},
    RunStatus.STOPPED: {RunStatus.STOPPED, RunStatus.PRUNED},
    RunStatus.FAILED: {RunStatus.FAILED, RunStatus.PRUNED},
    RunStatus.INTERRUPTED: {RunStatus.INTERRUPTED, RunStatus.PRUNED},
    RunStatus.PRUNED: {RunStatus.PRUNED},
}


class IllegalRunTransition(ValueError):
    """A status was written that cannot follow the run's current one."""


def settle(*, stopped: bool, failed: bool) -> RunStatus:
    """How a capture task ended: it raised, it was stopped, or it ran out."""
    if failed:
        return RunStatus.FAILED
    return RunStatus.STOPPED if stopped else RunStatus.COMPLETE


def transition(current: str, new: str) -> RunStatus:
    """``new`` as a status when it may follow ``current``, else raise."""
    cur, nxt = RunStatus(current), RunStatus(new)
    if nxt not in _NEXT[cur]:
        raise IllegalRunTransition(f"a {cur.value} run cannot become {nxt.value}")
    return nxt
