"""Unit tests for ``ProcessSupervisor.stop_popen``'s Windows escalation (#530).

Regression for the divergence the #530 audit flagged: every ``stop_popen``
caller (``backend_process.stop``, ``tts_engines.orpheus``) sent
``CTRL_BREAK_EVENT`` and immediately called ``terminate()`` — a hard
``TerminateProcess`` on Windows — with no grace period for the graceful
signal to actually land. ``tray/tray.py``'s hand-rolled ``HubProcess.stop``
had always waited between the two, which is the behavior that actually
lets a process shut down cleanly. #530 folds the correct sequence back into
the shared helper (and drops the tray's now-redundant duplicate).

Fakes a ``subprocess.Popen``-shaped object rather than spawning a real
process — the escalation *sequencing* is what's under test, not OS signal
delivery, and a fake keeps this deterministic and platform-independent to
run (only the Windows branch is exercised via ``monkeypatch.setattr(sys,
"platform", "win32")``).
"""

from __future__ import annotations

import subprocess
import sys

from src.process.process_supervisor import (
    OutcomeKind,
    ProcessOutcome,
    ProcessSupervisor,
)


class _FakePopen:
    """Records calls; ``dies_after`` names which call makes ``wait()`` succeed."""

    def __init__(self, *, dies_after: str) -> None:
        self.dies_after = dies_after
        self.calls: list[str] = []
        self._alive = True

    def send_signal(self, _sig) -> None:
        self.calls.append("send_signal")
        if self.dies_after == "send_signal":
            self._alive = False

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self.dies_after == "terminate":
            self._alive = False

    def kill(self) -> None:
        self.calls.append("kill")
        self._alive = False

    def wait(self, timeout: float) -> None:
        self.calls.append("wait")
        if self._alive:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)


def test_ctrl_break_given_a_grace_period_before_terminate(monkeypatch):
    """A process that dies from CTRL_BREAK alone must never see terminate()."""
    monkeypatch.setattr(sys, "platform", "win32")
    proc = _FakePopen(dies_after="send_signal")

    outcome = ProcessSupervisor.stop_popen(proc, terminate_timeout=5, kill_timeout=5)

    assert outcome == ProcessOutcome(OutcomeKind.STOPPED, "stopped")
    assert proc.calls == ["send_signal", "wait"]   # no terminate/kill needed


def test_escalates_to_terminate_only_after_ctrl_break_grace_expires(monkeypatch):
    """A process that ignores CTRL_BREAK escalates to terminate() next."""
    monkeypatch.setattr(sys, "platform", "win32")
    proc = _FakePopen(dies_after="terminate")

    outcome = ProcessSupervisor.stop_popen(proc, terminate_timeout=5, kill_timeout=5)

    assert outcome == ProcessOutcome(OutcomeKind.STOPPED, "stopped")
    # CTRL_BREAK's own wait must time out (raise) *before* terminate() is
    # ever called — the exact ordering the pre-#530 code skipped.
    assert proc.calls == ["send_signal", "wait", "terminate", "wait"]


def test_escalates_to_kill_as_last_resort(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    proc = _FakePopen(dies_after="kill")

    outcome = ProcessSupervisor.stop_popen(proc, terminate_timeout=5, kill_timeout=5)

    assert outcome == ProcessOutcome(OutcomeKind.STOPPED, "stopped")
    assert proc.calls == ["send_signal", "wait", "terminate", "wait", "kill", "wait"]


def test_non_windows_skips_ctrl_break_entirely(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    proc = _FakePopen(dies_after="terminate")

    outcome = ProcessSupervisor.stop_popen(proc, terminate_timeout=5, kill_timeout=5)

    assert outcome == ProcessOutcome(OutcomeKind.STOPPED, "stopped")
    assert proc.calls == ["terminate", "wait"]   # no send_signal on non-Windows


# --------------------------------------------------------------------------- #
# Typed outcomes: each kind keeps the detail text logs, toasts and older peers
# already see — the kind is the contract, the detail stays byte-identical.
# --------------------------------------------------------------------------- #
class _SpawnedProc:
    pid = 4321


def _supervisor(monkeypatch, *, running=False, reachable=False, build=None, ext_pid=None):
    from pathlib import Path

    from src.process import process_supervisor as ps

    recorded: list = []
    monkeypatch.setattr(ps.subprocess, "Popen", lambda *a, **k: _SpawnedProc())

    def default_build():
        return ps.SpawnSpec(cmd=["x"], cwd=Path("."), env={}, stdout=None, stderr=None)

    return ProcessSupervisor(
        already_running=lambda: running,
        reachable=lambda: reachable,
        external_pid=lambda: ext_pid,
        build_spawn_spec=build or default_build,
        set_process=recorded.append,
        adopt_message="adopted external instance",
    ), recorded


def test_start_outcome_kinds_keep_their_detail_text(monkeypatch):
    sup, _ = _supervisor(monkeypatch, running=True)
    assert sup.start() == ProcessOutcome(OutcomeKind.ALREADY_RUNNING, "already running")

    sup, _ = _supervisor(monkeypatch, reachable=True, ext_pid=99)
    assert sup.start() == ProcessOutcome(OutcomeKind.ADOPTED, "adopted external instance (PID 99)", 99)

    sup, _ = _supervisor(monkeypatch, reachable=True)
    assert sup.start() == ProcessOutcome(OutcomeKind.ADOPTED, "adopted external instance")

    def missing_binary():
        raise RuntimeError("llama-server binary not found")

    sup, recorded = _supervisor(monkeypatch, build=missing_binary)
    assert sup.start() == ProcessOutcome(
        OutcomeKind.LAUNCH_FAILED, "failed to launch: llama-server binary not found")
    assert recorded == []

    sup, recorded = _supervisor(monkeypatch)
    assert sup.start() == ProcessOutcome(OutcomeKind.STARTED, "started (pid=4321)", 4321)
    assert len(recorded) == 1


def test_stop_failure_keeps_its_detail_text(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    class _Stuck:
        def terminate(self):
            raise PermissionError("access denied")

    outcome = ProcessSupervisor.stop_popen(_Stuck(), terminate_timeout=1, kill_timeout=1)
    assert outcome == ProcessOutcome(OutcomeKind.STOP_FAILED, "error stopping: access denied")


def test_ok_and_converged_are_derived_from_the_kind():
    ok = {OutcomeKind.STARTED, OutcomeKind.ADOPTED, OutcomeKind.STOPPED}
    converged = ok | {OutcomeKind.ALREADY_RUNNING, OutcomeKind.NOT_RUNNING}
    for kind in OutcomeKind:
        outcome = ProcessOutcome(kind, "x")
        assert outcome.ok is (kind in ok)
        assert outcome.converged is (kind in converged)


def test_backend_start_and_stop_refusals_keep_their_detail_text(monkeypatch):
    from src.process import backend_process as bp

    monkeypatch.setattr(bp, "resolve_model_by_id", lambda mid: None)
    assert bp.start("ghost") == ProcessOutcome(
        OutcomeKind.NOT_ENABLED, "model 'ghost' not enabled on this host")
    assert bp.stop("never-started-model") == ProcessOutcome(OutcomeKind.NOT_RUNNING, "not running")
