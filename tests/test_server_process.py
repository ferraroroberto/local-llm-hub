"""Regression coverage for cross-platform hub process discovery."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

from src import host_profile, server_process


def test_find_port_pids_reads_the_shared_listener_snapshot(monkeypatch):
    monkeypatch.setattr(
        server_process, "snapshot_listening_pids", lambda: {8098: [40368], 8000: [1]}
    )

    assert server_process.find_port_pids(8098) == [40368]
    assert server_process.find_port_pids(9999) == []


def test_snapshot_listening_pids_posix_lsof_fallback(monkeypatch):
    """With psutil unusable, the one lsof parser serves every port lookup."""
    import psutil

    def denied(**_kwargs):
        raise psutil.AccessDenied()

    def fake_run(command, **_kwargs):
        assert command[0] == "lsof"
        return SimpleNamespace(
            stdout="p40368\nPTCP\nn*:8098\np77\nPTCP\nn127.0.0.1:8000\n"
        )

    monkeypatch.setattr(psutil, "net_connections", denied)
    monkeypatch.setattr(server_process.sys, "platform", "darwin")
    monkeypatch.setattr(server_process.subprocess, "run", fake_run)

    assert server_process.find_port_pids(8098) == [40368]
    assert server_process.find_port_pids(8000) == [77]
    assert server_process.find_port_pids(1234) == []


def test_port_and_base_url_follow_hub_port_from_config(write_config, monkeypatch):
    """#483 — server_process must not hardcode :8000; it must track hub.port.

    Before the fix, PORT/BASE_URL were module-level literals, so changing
    `hub.port` in models.yaml only moved the bind (server.main() reads
    hub_port() directly) — every consumer of server_process's constants
    (the adoption probe, is_reachable, the watchdog URL) kept dialing the
    old hardcoded :8000, silently adopting an unrelated process.
    """
    original_config_path = host_profile.CONFIG_PATH
    write_config({"hub": {"port": 8020, "bind_host": "127.0.0.2"}})
    try:
        importlib.reload(server_process)
        assert server_process.PORT == 8020
        assert server_process.BIND_HOST == "127.0.0.2"
        assert server_process.BASE_URL == "http://127.0.0.1:8020"
    finally:
        monkeypatch.setattr(host_profile, "CONFIG_PATH", original_config_path)
        host_profile._CONFIG_CACHE.clear()
        importlib.reload(server_process)


def test_port_and_base_url_default_unchanged(write_config, monkeypatch):
    """Acceptance: default config (no `hub` section) behaves as today."""
    original_config_path = host_profile.CONFIG_PATH
    write_config({})
    try:
        importlib.reload(server_process)
        assert server_process.PORT == 8000
        assert server_process.BIND_HOST == "0.0.0.0"
        assert server_process.BASE_URL == "http://127.0.0.1:8000"
    finally:
        monkeypatch.setattr(host_profile, "CONFIG_PATH", original_config_path)
        host_profile._CONFIG_CACHE.clear()
        importlib.reload(server_process)
