"""The keep-backends flag holds only while a hub restart is really in flight.

``POST /admin/api/hub/restart`` sets ``backend_process.restart_pending`` so the
shutdown handler leaves model backends running for the respawned hub to adopt.
If the restart never happens the process stays up with the flag still set, and
the next ordinary stop would orphan every backend. Each failure path must drop
the flag; the success path must keep it. The endpoint coroutine is called
directly and the platform is faked on the router's own ``sys`` reference, so no
real supervisor, signal or process is touched.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import types

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest

import app_web.routers.hub as hub_mod
from src.process import backend_process as bp
from src.process import hub_process_control as hpc


@pytest.fixture(autouse=True)
def _reset_restart_flag():
    bp.set_restart_pending(False)
    yield
    bp.set_restart_pending(False)


def _restart() -> dict:
    return asyncio.run(hub_mod.hub_restart())


def _fake_platform(monkeypatch, platform: str) -> None:
    monkeypatch.setattr(hub_mod, "sys", types.SimpleNamespace(platform=platform))
    monkeypatch.setattr(hub_mod, "_under_systemd", lambda: False)


def _darwin(monkeypatch, run) -> None:
    _fake_platform(monkeypatch, "darwin")
    monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(hub_mod.subprocess, "run", run)


# ------------------------------------------------------------------ darwin


def test_darwin_kickstart_timeout_clears_flag(monkeypatch):
    def run(*_a, **_k):
        raise subprocess.TimeoutExpired("launchctl", 30)

    _darwin(monkeypatch, run)
    out = _restart()
    assert out["ok"] is False
    assert bp.restart_pending() is False


def test_darwin_kickstart_nonzero_exit_clears_flag(monkeypatch):
    _darwin(monkeypatch, lambda *_a, **_k: subprocess.CompletedProcess([], 113, b"", b"no such service"))
    out = _restart()
    assert out["ok"] is False
    assert bp.restart_pending() is False


def test_darwin_kickstart_exception_clears_flag(monkeypatch):
    def run(*_a, **_k):
        raise FileNotFoundError("launchctl")

    _darwin(monkeypatch, run)
    with pytest.raises(FileNotFoundError):
        _restart()
    assert bp.restart_pending() is False


def test_darwin_kickstart_success_keeps_flag(monkeypatch):
    _darwin(monkeypatch, lambda *_a, **_k: subprocess.CompletedProcess([], 0, b"", b""))
    out = _restart()
    assert out["ok"] is True
    assert bp.restart_pending() is True


# ------------------------------------------------------------ watchdog path


def test_watchdog_spawn_exception_clears_flag(monkeypatch):
    _fake_platform(monkeypatch, "win32")
    monkeypatch.setattr(hub_mod, "_delayed_shutdown", lambda **_k: pytest.fail("must not shut down"))

    def spawn():
        raise OSError("cannot spawn")

    monkeypatch.setattr(hub_mod, "_spawn_respawn_watchdog", spawn)
    with pytest.raises(OSError):
        _restart()
    assert bp.restart_pending() is False


def test_watchdog_success_keeps_flag(monkeypatch):
    _fake_platform(monkeypatch, "win32")
    monkeypatch.setattr(hub_mod, "_spawn_respawn_watchdog", lambda: None)
    monkeypatch.setattr(hub_mod, "_delayed_shutdown", lambda **_k: None)
    assert _restart()["ok"] is True
    assert bp.restart_pending() is True


# ----------------------------------------------------------------- systemd


class _InlineThread:
    """Run the delayed-systemctl worker synchronously so the test can observe it."""

    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _systemctl(monkeypatch, run) -> None:
    monkeypatch.setattr(hpc.threading, "Thread", _InlineThread)
    monkeypatch.setattr(hpc.time, "sleep", lambda _s: None)
    monkeypatch.setattr(hpc.subprocess, "run", run)
    monkeypatch.setattr(hub_mod, "sys", types.SimpleNamespace(platform="linux"))
    monkeypatch.setattr(hub_mod, "_under_systemd", lambda: True)
    monkeypatch.setattr(hub_mod, "_delayed_systemctl", hpc._delayed_systemctl)


def test_systemctl_timeout_clears_flag(monkeypatch):
    def run(*_a, **_k):
        raise subprocess.TimeoutExpired("systemctl", 30)

    _systemctl(monkeypatch, run)
    _restart()
    assert bp.restart_pending() is False


def test_systemctl_nonzero_exit_clears_flag(monkeypatch):
    _systemctl(monkeypatch, lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", "sudo: a password is required"))
    _restart()
    assert bp.restart_pending() is False


def test_systemctl_cannot_start_clears_flag(monkeypatch):
    def run(*_a, **_k):
        raise FileNotFoundError("sudo")

    _systemctl(monkeypatch, run)
    _restart()
    assert bp.restart_pending() is False


def test_systemctl_success_keeps_flag(monkeypatch):
    _systemctl(monkeypatch, lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""))
    assert _restart()["ok"] is True
    assert bp.restart_pending() is True


def test_systemctl_stop_failure_does_not_touch_flag(monkeypatch):
    """A failed ``stop`` passes no ``on_failure``, so it cannot clear a restart's flag."""
    _systemctl(monkeypatch, lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", "boom"))
    bp.set_restart_pending(True)
    hpc._delayed_systemctl("stop")
    assert bp.restart_pending() is True
