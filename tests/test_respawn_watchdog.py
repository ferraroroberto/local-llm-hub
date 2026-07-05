"""Unit tests for src/_respawn_watchdog.py (issue #198).

This module used to be a ~60-line string literal built up line-by-line
and fed to ``python -c`` from app_web/routers/hub.py's
_spawn_respawn_watchdog() — unlintable, untestable, no type-checking.
Now a real module with directly-testable pure helpers.
"""

from __future__ import annotations

import socket
import sys

import pytest

from src import _respawn_watchdog as watchdog


def test_is_alive_true_for_current_process():
    import os
    assert watchdog.is_alive(os.getpid()) is True


def test_is_alive_false_for_a_pid_that_does_not_exist():
    # A PID far beyond any plausible live process on a dev/CI box.
    assert watchdog.is_alive(999_999) is False


def test_port_is_free_reports_free_port():
    # Bind to port 0 to get an OS-assigned free port, then release it.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert watchdog.port_is_free(port) is True


def test_port_is_free_reports_occupied_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert watchdog.port_is_free(port) is False
    finally:
        s.close()


def test_port_is_reachable_true_when_something_listens():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert watchdog.port_is_reachable(port) is True
    finally:
        s.close()


def test_port_is_reachable_false_when_nothing_listens():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # freed — nothing listening now
    assert watchdog.port_is_reachable(port) is False


def test_relaunch_executable_normalises_pythonw_to_python(monkeypatch, tmp_path):
    fake_pythonw = tmp_path / "pythonw.exe"
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("")  # just needs to exist
    monkeypatch.setattr(sys, "executable", str(fake_pythonw))
    assert watchdog.relaunch_executable() == str(fake_python)


def test_relaunch_executable_passthrough_when_already_python(monkeypatch, tmp_path):
    fake_python = tmp_path / "python.exe"
    monkeypatch.setattr(sys, "executable", str(fake_python))
    assert watchdog.relaunch_executable() == str(fake_python)


def test_relaunch_executable_passthrough_when_pythonw_has_no_sibling(monkeypatch, tmp_path):
    # pythonw.exe with no sibling python.exe on disk — keep pythonw.exe
    # rather than pointing at a binary that doesn't exist.
    fake_pythonw = tmp_path / "pythonw.exe"
    monkeypatch.setattr(sys, "executable", str(fake_pythonw))
    assert watchdog.relaunch_executable() == str(fake_pythonw)


def test_main_requires_all_four_args(capsys):
    with pytest.raises(SystemExit):
        watchdog.main([])


def test_main_parses_args_and_invokes_run(monkeypatch):
    captured = {}

    def fake_run(parent_pid, port, log_path, root):
        captured.update(parent_pid=parent_pid, port=port, log_path=log_path, root=root)

    monkeypatch.setattr(watchdog, "run", fake_run)
    rc = watchdog.main([
        "--parent-pid", "1234",
        "--port", "8000",
        "--log-path", "/tmp/x.log",
        "--root", "/tmp/root",
    ])
    assert rc == 0
    assert captured == {
        "parent_pid": 1234, "port": 8000, "log_path": "/tmp/x.log", "root": "/tmp/root",
    }


def test_run_full_orchestration_waits_dies_relaunches_and_logs_success(monkeypatch, tmp_path):
    """Integration test of run() itself: a real short-lived subprocess
    stands in for the dying hub; subprocess.Popen for the *relaunch* is
    monkeypatched so the test doesn't actually launch src.server — it just
    proves run() sequences wait-for-parent-death -> wait-for-port-free ->
    relaunch -> poll-for-reachable -> log correctly.
    """
    import subprocess as sp
    import time as _time

    # A short-lived real process to be "parent" — dies almost immediately.
    dying_parent = sp.Popen([sys.executable, "-c", "pass"])
    dying_parent.wait(timeout=5)

    # A free port to claim as "our" port, released before run() checks it.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    log_path = tmp_path / "respawn.log"
    relaunch_calls = []
    real_popen = sp.Popen

    class _FakeChild:
        def __init__(self):
            self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._listener.bind(("127.0.0.1", port))
            self._listener.listen(1)
            self.pid = 4242

        def poll(self):
            return None  # still "running"

    fake_child_holder = {}

    def fake_popen(cmd, **kwargs):
        # Only intercept run()'s own relaunch Popen call — is_alive() on
        # Windows shells out via subprocess.run (which uses Popen
        # internally too), and that must hit the real implementation.
        if isinstance(cmd, list) and len(cmd) >= 3 and cmd[1:3] == ["-m", "src.server"]:
            relaunch_calls.append(cmd)
            fake_child_holder["child"] = _FakeChild()
            return fake_child_holder["child"]
        return real_popen(cmd, **kwargs)

    monkeypatch.setattr(sp, "Popen", fake_popen)

    watchdog.run(dying_parent.pid, port, str(log_path), str(tmp_path))

    assert relaunch_calls, "expected a relaunch subprocess.Popen call"
    assert relaunch_calls[0][1:3] == ["-m", "src.server"]
    log_text = log_path.read_text(encoding="utf-8")
    assert "relaunching" in log_text
    assert "hub back up" in log_text
    fake_child_holder["child"]._listener.close()
