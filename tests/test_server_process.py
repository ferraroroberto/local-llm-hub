"""Regression coverage for cross-platform hub process discovery."""

from __future__ import annotations

from types import SimpleNamespace

from src import server_process


def test_find_port_pids_constrains_posix_lsof_to_requested_listener(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="40368\n")

    monkeypatch.setattr(server_process.sys, "platform", "darwin")
    monkeypatch.setattr(server_process.subprocess, "run", fake_run)

    assert server_process.find_port_pids(8098) == [40368]
    assert calls == [[
        "lsof",
        "-nP",
        "-a",
        "-iTCP:8098",
        "-sTCP:LISTEN",
        "-t",
    ]]
