"""Per-model serving state, derived in one place.

A backend's process being alive says nothing about whether it answers: a
llama-server reading 13 GB of weights, a whisper row that crashed during load
and a backend that hung are all "alive or not" without being ready. This
module names the states a reader needs and derives them from one set of
observations, so the admin payload, the SPA tile, the audio health verdict
and the fleet placement summary cannot disagree about a model.

States:

* ``stopped`` — no process of ours, nothing on the port.
* ``loading`` — ours, spawned (or inherited) and not yet answering, still
  inside the load deadline.
* ``ready`` — ours and answering.
* ``adopted`` — someone else's process holds the port and is confirmed to be
  this model's server binary (the hub routes to it but did not start it).
* ``failed`` — ours died, or never answered within the deadline, or stopped
  answering after it was ready; ``reason`` says which.
* ``unknown`` — a fact needed to tell the states apart could not be gathered
  (the port snapshot failed, or the port holder's identity could not be
  read), or the port holder is some other program.

:func:`derive` is pure apart from latching ``LoadRecord.ready`` the first
time a live process answers; the per-model ``LoadRecord`` lives in
:mod:`src.process.backend_process`, which owns the process handles.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from .server_process import OWNERSHIP_EXTERNAL, OWNERSHIP_NONE, OWNERSHIP_OURS

# How long a spawned backend may take to answer before it reads as failed.
# Sized for the heaviest row (gemma4_26b's 13.4 GB read from NVMe), the same
# budget the on-demand first request waits for (``on_demand.READY_DEADLINE_S``).
LOAD_DEADLINE_S = 180.0


class ServingState(str, Enum):
    """What a model is doing right now. Values travel as ``serving_state``."""

    STOPPED = "stopped"
    LOADING = "loading"
    READY = "ready"
    ADOPTED = "adopted"
    FAILED = "failed"
    UNKNOWN = "unknown"


# States in which the model serves requests — what "running" means to a reader.
SERVING = frozenset({ServingState.READY, ServingState.ADOPTED})


@dataclass
class LoadRecord:
    """One spawn (or inheritance) of a backend the hub owns.

    ``started_at`` is ``time.monotonic()`` at spawn; ``ready`` latches the
    first time the live process answers, so a later loss of the probe reads
    as "stopped answering" rather than "still loading".
    """

    started_at: float
    ready: bool = False


@dataclass(frozen=True)
class Observation:
    """What a reader saw for one model at one moment."""

    alive: bool                       # our Popen or inherited PID is alive
    reachable: bool                   # the backend's own probe answered
    port_known: bool                  # the listening-port snapshot succeeded
    port_foreign: bool                # someone else listens on the port
    exit_code: Optional[int] = None   # our Popen's exit code once it died
    pid: Optional[int] = None         # ours while alive, else the port holder's
    # The foreign port holder is this model's server binary: True confirmed,
    # False another program, None could not be read. Unset reads as
    # unconfirmed, so a reader that skips the check cannot produce ``adopted``.
    holder_confirmed: Optional[bool] = None


@dataclass(frozen=True)
class Serving:
    """A derived state plus the reason a ``failed``/``unknown`` state carries.

    ``ownership`` and ``pid`` are the older tri-state fields, derived here too
    so no reader computes them on its own. An unknown snapshot keeps
    ``ownership = none``, the value older clients already read; ``state`` is
    what tells it apart from a confirmed stop.
    """

    state: ServingState
    reason: Optional[str] = None
    ownership: str = OWNERSHIP_NONE
    pid: Optional[int] = None

    def payload(self) -> Dict[str, Any]:
        """The fields added to an admin row / audio health entry."""
        out: Dict[str, Any] = {"serving_state": self.state.value}
        if self.reason:
            out["serving_reason"] = self.reason
        return out


def _exited(exit_code: Optional[int], during_load: bool) -> str:
    when = "during load" if during_load else "after it was ready"
    code = f" (exit code {exit_code})" if exit_code is not None else ""
    return f"process exited {when}{code}"


def derive(
    obs: Observation,
    load: Optional[LoadRecord],
    now: float,
    deadline_s: float = LOAD_DEADLINE_S,
) -> Serving:
    """The one derivation of a model's serving state from what a reader saw.

    ``load`` is the model's current spawn record (``None`` when the hub never
    spawned it or stopped it on purpose). A live process that answers latches
    ``load.ready``.
    """
    if obs.alive:
        ours = {"ownership": OWNERSHIP_OURS, "pid": obs.pid}
        if obs.reachable:
            if load is not None:
                load.ready = True
            return Serving(ServingState.READY, **ours)
        if load is None or load.ready:
            return Serving(ServingState.FAILED, "process is alive but stopped answering", **ours)
        if now - load.started_at > deadline_s:
            return Serving(
                ServingState.FAILED,
                f"did not answer within {deadline_s:.0f}s of starting", **ours,
            )
        return Serving(ServingState.LOADING, **ours)
    # Not alive, but the hub never stopped it: it died on its own.
    if load is not None:
        return Serving(ServingState.FAILED, _exited(obs.exit_code, not load.ready))
    if not obs.port_known:
        return Serving(ServingState.UNKNOWN, "listening-port snapshot failed")
    if obs.port_foreign:
        if obs.holder_confirmed:
            return Serving(ServingState.ADOPTED, ownership=OWNERSHIP_EXTERNAL, pid=obs.pid)
        if obs.holder_confirmed is False:
            return Serving(ServingState.UNKNOWN, "port holder is not this model's server binary")
        return Serving(ServingState.UNKNOWN, "port holder's identity could not be confirmed")
    return Serving(ServingState.STOPPED)
