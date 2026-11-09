"""Shared subprocess lifecycle helpers for hub and model backends.

The hub process and each local model backend have different launch details
(log pipe vs. log file, singleton vs. keyed state, inherited PID support), but
share the same start/stop lifecycle: reject already-running processes, adopt a
reachable external listener, spawn with UTF-8 env, and terminate with a polite
then forceful shutdown.  This module keeps that workflow in one place while the
callers provide their process-specific policy.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import IO, Callable, Optional

# Stop budgets for a model-backend child (llama-server, the TTS server). The
# polite window is long enough for a backend to flush its KV cache / close its
# listener after CTRL_BREAK or terminate(); the kill window only covers the OS
# reaping a process that was already force-killed, so it is shorter.
STOP_TERMINATE_TIMEOUT_S = 8.0
STOP_KILL_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class SpawnSpec:
    """Concrete subprocess launch configuration."""

    cmd: list[str]
    cwd: Path
    env: dict[str, str]
    stdout: int | IO[bytes] | IO[str] | None
    stderr: int | IO[bytes] | IO[str] | None
    text: bool = False
    encoding: str | None = None
    errors: str | None = None
    bufsize: int = -1
    creationflags: int = 0


class OutcomeKind(str, Enum):
    """What a process start or stop actually did.

    Callers branch on this, never on ``ProcessOutcome.detail``: the detail is
    human text (logs, toasts, peers on older builds) and may be reworded, the
    kind is the contract. The values travel over HTTP as the admin route's
    ``outcome`` field, so they never change once shipped.
    """

    STARTED = "started"
    ADOPTED = "adopted"
    ALREADY_RUNNING = "already_running"
    LAUNCH_FAILED = "launch_failed"
    NOT_ENABLED = "not_enabled"
    NOT_OWNER = "not_owner"
    STOPPED = "stopped"
    NOT_RUNNING = "not_running"
    STOP_FAILED = "stop_failed"


_OK_KINDS = frozenset({OutcomeKind.STARTED, OutcomeKind.ADOPTED, OutcomeKind.STOPPED})
# The process already sits in the requested state, whether or not this call
# changed it — what an idempotent caller (autostart, reconcile) waits for.
_CONVERGED_KINDS = _OK_KINDS | {OutcomeKind.ALREADY_RUNNING, OutcomeKind.NOT_RUNNING}


@dataclass(frozen=True)
class ProcessOutcome:
    """Typed result of a start/stop: ``kind`` for code, ``detail`` for people."""

    kind: OutcomeKind
    detail: str
    pid: Optional[int] = None

    @property
    def ok(self) -> bool:
        """This call changed the process into the requested state (or adopted it)."""
        return self.kind in _OK_KINDS

    @property
    def converged(self) -> bool:
        """The process is in the requested state, whoever put it there."""
        return self.kind in _CONVERGED_KINDS


class ProcessSupervisor:
    """Parameterised process start/stop workflow.

    ``start`` owns the common orchestration; callers only supply how to check
    reachability, find external PIDs, build a ``SpawnSpec``, and record the
    resulting ``Popen``. ``stop_popen`` centralises the Windows CTRL_BREAK /
    terminate / kill fallback sequence while preserving per-caller timeouts.
    """

    def __init__(
        self,
        *,
        already_running: Callable[[], bool],
        reachable: Callable[[], bool],
        external_pid: Callable[[], Optional[int]],
        build_spawn_spec: Callable[[], SpawnSpec],
        set_process: Callable[[Optional[subprocess.Popen]], None],
        on_spawned: Callable[[subprocess.Popen], None] | None = None,
        adopt_message: str,
    ) -> None:
        self._already_running = already_running
        self._reachable = reachable
        self._external_pid = external_pid
        self._build_spawn_spec = build_spawn_spec
        self._set_process = set_process
        self._on_spawned = on_spawned
        self._adopt_message = adopt_message

    def start(self) -> ProcessOutcome:
        if self._already_running():
            return ProcessOutcome(OutcomeKind.ALREADY_RUNNING, "already running")

        if self._reachable():
            ext_pid = self._external_pid()
            suffix = f" (PID {ext_pid})" if ext_pid else ""
            return ProcessOutcome(OutcomeKind.ADOPTED, f"{self._adopt_message}{suffix}", ext_pid)

        try:
            spec = self._build_spawn_spec()
            proc = subprocess.Popen(
                spec.cmd,
                cwd=str(spec.cwd),
                stdout=spec.stdout,
                stderr=spec.stderr,
                text=spec.text,
                encoding=spec.encoding,
                errors=spec.errors,
                bufsize=spec.bufsize,
                env=spec.env,
                creationflags=spec.creationflags,
            )
        except Exception as e:
            return ProcessOutcome(OutcomeKind.LAUNCH_FAILED, f"failed to launch: {e}")

        self._set_process(proc)
        if self._on_spawned is not None:
            self._on_spawned(proc)
        return ProcessOutcome(OutcomeKind.STARTED, f"started (pid={proc.pid})", proc.pid)

    @staticmethod
    def stop_popen(
        proc: subprocess.Popen,
        *,
        terminate_timeout: float,
        kill_timeout: float,
    ) -> ProcessOutcome:
        try:
            if sys.platform == "win32":
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except (OSError, ValueError):
                    pass
                else:
                    # Give the child's own graceful-shutdown handler a
                    # chance to run before escalating — terminate() is an
                    # immediate hard TerminateProcess on Windows, so calling
                    # it right away never let a
                    # just-sent CTRL_BREAK actually land. tray/tray.py's
                    # HubProcess.stop always waited at this point; every
                    # other stop_popen caller escalated instantly instead —
                    # a divergence that made this the *worse* of two
                    # duplicate stop sequences.
                    try:
                        proc.wait(timeout=terminate_timeout)
                        return ProcessOutcome(OutcomeKind.STOPPED, "stopped")
                    except subprocess.TimeoutExpired:
                        pass
            proc.terminate()
            try:
                proc.wait(timeout=terminate_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=kill_timeout)
        except Exception as e:
            return ProcessOutcome(OutcomeKind.STOP_FAILED, f"error stopping: {e}")
        return ProcessOutcome(OutcomeKind.STOPPED, "stopped")
