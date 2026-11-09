"""Unit tests for ``tray.tray.HubProcess`` driving the shared
``ProcessSupervisor`` (#530) instead of a second hand-rolled start/stop.

These fake ``ProcessSupervisor`` itself (a class-level monkeypatch of the
name imported into ``tray.tray``) rather than spawning real subprocesses —
``HubProcess``'s own job is wiring ``already_running``/``reachable``/
``build_spawn_spec``/``set_process`` correctly and passing the supervisor's
typed outcome through; the supervisor's internal escalation logic is
covered by ``tests/backends/test_process_supervisor.py``. The tray workers
below branch on that outcome's kind, never on its detail text.
"""

from __future__ import annotations

import os
import types

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

from src.process.process_supervisor import OutcomeKind, ProcessOutcome  # noqa: E402
from tray import tray as tray_mod  # noqa: E402


class _FakeSupervisor:
    """Records the kwargs ``HubProcess.start()`` wired it with and returns
    a scripted outcome from ``start()``."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _FakeSupervisor.last_kwargs = kwargs

    def start(self):
        return _FakeSupervisor.result


def _fake_supervisor(result):
    _FakeSupervisor.result = result
    return _FakeSupervisor


class _FakeAliveProc:
    def poll(self):
        return None   # still running


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _hub_with(monkeypatch, outcome):
    monkeypatch.setattr(tray_mod, "ProcessSupervisor", _fake_supervisor(outcome))
    monkeypatch.setattr(tray_mod, "cross_process_lock", lambda name: _NullCtx())
    return tray_mod.HubProcess()


def test_start_passes_the_supervisor_outcome_through(monkeypatch):
    for outcome in (
        ProcessOutcome(OutcomeKind.ALREADY_RUNNING, "already running"),
        ProcessOutcome(OutcomeKind.ADOPTED, "adopted external hub"),
        ProcessOutcome(OutcomeKind.LAUNCH_FAILED, "failed to launch: boom"),
    ):
        assert _hub_with(monkeypatch, outcome).start() == outcome


def test_start_wires_the_hub_module_as_the_spawn_command(monkeypatch):
    hub = _hub_with(monkeypatch, ProcessOutcome(OutcomeKind.STARTED, "started (pid=1)", 1))
    hub.start()

    spec = _FakeSupervisor.last_kwargs["build_spawn_spec"]()
    assert spec.cmd[-2:] == ["-m", "src.server"]
    assert spec.creationflags == tray_mod.WIN_NEW_GROUP


def test_stop_delegates_to_shared_stop_popen(monkeypatch):
    calls = []
    stopped = ProcessOutcome(OutcomeKind.STOPPED, "stopped")
    monkeypatch.setattr(
        tray_mod.ProcessSupervisor, "stop_popen",
        staticmethod(lambda proc, **kw: (calls.append((proc, kw)), stopped)[1]),
    )

    hub = tray_mod.HubProcess()
    fake_proc = _FakeAliveProc()
    hub.proc = fake_proc

    assert hub.stop() == stopped
    assert calls and calls[0][0] is fake_proc
    assert hub.proc is None


def test_stop_of_an_adopted_hub_is_not_owner(monkeypatch):
    hub = tray_mod.HubProcess()
    monkeypatch.setattr(hub, "is_reachable", lambda timeout: True)
    assert hub.stop() == ProcessOutcome(OutcomeKind.NOT_OWNER, "adopted (not ours to stop)")
    monkeypatch.setattr(hub, "is_reachable", lambda timeout: False)
    assert hub.stop() == ProcessOutcome(OutcomeKind.NOT_RUNNING, "not running")


# --------------------------------------------------------------------------- #
# TrayApp workers — each branch reads the outcome kind
# --------------------------------------------------------------------------- #
class _FakeHub:
    def __init__(self, start_outcome, stop_outcome=None):
        self.start_outcome = start_outcome
        self.stop_outcome = stop_outcome
        self.starts = 0

    def start(self):
        self.starts += 1
        return self.start_outcome

    def stop(self):
        return self.stop_outcome

    def wait_ready(self, timeout_s):
        return True


def _app(monkeypatch, hub=None, responses=None):
    """A ``TrayApp`` without its constructor (no icon, no config, no models):
    records notifications and answers ``_admin_request`` from ``responses``."""
    app = object.__new__(tray_mod.TrayApp)
    app.cfg = types.SimpleNamespace(hub_ready_timeout_s=1.0)
    app.hub = hub
    app._models = [types.SimpleNamespace(id="qwen", port=8081)]
    app.notes = []
    app.requests = []
    app._notify = lambda title, message: app.notes.append(message)
    app._update_menu = lambda: None

    def admin_request(method, path):
        app.requests.append(path)
        return responses[path.rsplit("/", 1)[-1]]

    app._admin_request = admin_request
    monkeypatch.setattr(tray_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(tray_mod.threading, "Thread", lambda **kw: types.SimpleNamespace(start=lambda: None))
    return app


def test_autostart_treats_already_running_as_up(monkeypatch):
    app = _app(monkeypatch, _FakeHub(ProcessOutcome(OutcomeKind.ALREADY_RUNNING, "already running")))
    app._autostart_worker()
    assert app.notes == ["▶ already running"]


def test_autostart_reports_a_launch_failure(monkeypatch):
    app = _app(monkeypatch, _FakeHub(ProcessOutcome(OutcomeKind.LAUNCH_FAILED, "failed to launch: boom")))
    app._autostart_worker()
    assert app.notes == ["⚠️ failed to launch: boom"]


def test_restart_refuses_a_hub_the_tray_does_not_own(monkeypatch):
    hub = _FakeHub(
        ProcessOutcome(OutcomeKind.ADOPTED, "adopted external hub"),
        ProcessOutcome(OutcomeKind.NOT_OWNER, "adopted (not ours to stop)"),
    )
    app = _app(monkeypatch, hub)
    app._restart_worker()
    assert hub.starts == 0
    assert "can't restart it from here" in app.notes[-1]


def test_restart_proceeds_after_a_real_stop(monkeypatch):
    hub = _FakeHub(
        ProcessOutcome(OutcomeKind.STARTED, "started (pid=7)", 7),
        ProcessOutcome(OutcomeKind.STOPPED, "stopped"),
    )
    app = _app(monkeypatch, hub)
    app._restart_worker()
    assert hub.starts == 1
    assert app.notes[-1] == "✅ restarted"


def test_start_model_already_running_is_info_by_outcome(monkeypatch):
    # The detail is deliberately not the old wording: only the kind decides.
    app = _app(monkeypatch, responses={"start": (False, "busy", "already_running")})
    app._start_model_worker("qwen", autostart=False)
    assert app.notes == ["✅ busy"]


def test_start_model_failure_is_a_warning(monkeypatch):
    app = _app(monkeypatch, responses={"start": (False, "already running? no: failed", "launch_failed")})
    app._start_model_worker("qwen", autostart=False)
    assert app.notes == ["⚠️ already running? no: failed"]


def test_toggle_force_stops_only_on_a_not_running_outcome(monkeypatch):
    monkeypatch.setattr(tray_mod, "_tcp_reachable", lambda host, port, timeout: True)
    app = _app(monkeypatch, responses={
        "stop": (False, "not ours", "not_running"),
        "force-stop": (True, "killed", None),
    })
    app._toggle_model_worker("qwen")
    assert app.requests[-1].endswith("/force-stop")
    assert app.notes == ["💀 Force-stopped"]

    app = _app(monkeypatch, responses={"stop": (False, "not running anywhere", "stop_failed")})
    app._toggle_model_worker("qwen")
    assert not app.requests[-1].endswith("/force-stop")
    assert app.notes == ["⚠️ not running anywhere"]
