"""Process → fleet-app attribution (#315).

The mapping is what turns `python.exe x14` into `app-launcher: 3 procs`, so
these tests pin the precedence order and the OS-agnostic path handling.
"""

from __future__ import annotations

import json

import pytest

from src.diagnostics import attribution


@pytest.fixture()
def rules(tmp_path):
    """A small, explicit rule set — the real config is free to grow without
    breaking these assertions."""
    path = tmp_path / "apps.json"
    path.write_text(json.dumps({
        "fleet_roots": ["e:/automation", "~/automation"],
        "binaries": {"llama-server": "llama.cpp", "dockerd": "docker",
                     "chrome": "chrome"},
        "cmdline_contains": {"-m src.run_backend": "local-llm-hub"},
    }), encoding="utf-8")
    attribution.set_rules_path(path)
    yield
    attribution.set_rules_path(None)


def test_windows_venv_path_attributes_to_its_repo(rules):
    cmd = r"E:\automation\app-launcher\.venv\Scripts\python.exe -m app.main"
    assert attribution.attribute("python.exe", cmd) == "app-launcher"


def test_forward_slash_and_case_are_normalized(rules):
    cmd = "E:/Automation/Photo-OCR/.venv/bin/python -m src"
    assert attribution.attribute("python", cmd) == "photo-ocr"


def test_posix_home_root_attributes(rules):
    import os
    home = os.path.expanduser("~").replace("\\", "/")
    assert attribution.attribute("python3", f"{home}/automation/grocery/.venv/bin/python3 -m app") == "grocery"


def test_sibling_worktree_folds_into_its_repo(rules):
    """`local-llm-hub-wt-315` is the same app as `local-llm-hub` — a
    concurrent worktree must not appear as a separate app."""
    cmd = "E:/automation/local-llm-hub-wt-315/.venv/Scripts/python.exe -m src.server"
    assert attribution.attribute("python.exe", cmd) == "local-llm-hub"


def test_known_binary_maps_by_name(rules):
    assert attribution.attribute("llama-server.exe", "llama-server.exe --port 8088") == "llama.cpp"
    assert attribution.attribute("dockerd", "/usr/bin/dockerd") == "docker"


def test_exe_suffix_is_ignored(rules):
    assert attribution.attribute("chrome.exe", "chrome.exe --type=renderer") == "chrome"
    assert attribution.attribute("chrome", "chrome --type=renderer") == "chrome"


def test_fleet_root_wins_over_binary_name(rules):
    """A repo-owned binary belongs to that repo — the more specific fact."""
    cmd = "E:/automation/local-llm-hub/vendor/llama.cpp/llama-server.exe --port 8088"
    assert attribution.attribute("llama-server.exe", cmd) == "local-llm-hub"


def test_cmdline_substring_is_the_last_resort(rules):
    assert attribution.attribute("python.exe", "python.exe -m src.run_backend hub") == "local-llm-hub"


def test_unknown_process_is_unattributed(rules):
    """Not a failure mode — this bucket is the review list of things nobody
    has accounted for yet."""
    assert attribution.attribute("MysteryApp.exe", "C:/foo/MysteryApp.exe") == "unattributed"


def test_empty_input_is_unattributed(rules):
    assert attribution.attribute("", "") == "unattributed"


def test_missing_rules_file_degrades_to_unattributed(tmp_path):
    """A broken config must not break a capture."""
    attribution.set_rules_path(tmp_path / "nope.json")
    try:
        assert attribution.attribute("python.exe", "E:/automation/x/.venv/python.exe") == "unattributed"
    finally:
        attribution.set_rules_path(None)


def test_scan_processes_returns_attributed_rows():
    """Smoke test against the real machine: the scan must return rows, and
    every row must carry the keys the store writes."""
    rows = attribution.scan_processes()
    assert rows, "expected at least one process on a running machine"
    required = {"pid", "name", "cmdline", "app_id", "cpu_percent", "rss_bytes"}
    assert required <= set(rows[0])
    assert all(isinstance(r["app_id"], str) and r["app_id"] for r in rows)


def test_windows_idle_process_is_excluded():
    """PID 0 on Windows is a placeholder for idle cycles, not a process:
    psutil reports its CPU as ncores x idle-fraction (~1400% on a quiet
    16-core box), which made the idle process rank as the busiest thing on
    the machine and inverted the whole CPU picture."""
    assert attribution._is_idle_placeholder("System Idle Process")
    assert attribution._is_idle_placeholder("system idle process")
    names = {(r.get("name") or "").lower() for r in attribution.scan_processes()}
    assert "system idle process" not in names


def test_macos_kernel_task_is_not_excluded():
    """macOS's PID 0 is `kernel_task` — real work that must keep being
    measured. The exclusion is by name, never by PID."""
    assert not attribution._is_idle_placeholder("kernel_task")
    assert not attribution._is_idle_placeholder("System")


def test_cmdline_is_trimmed():
    trimmed = attribution._trim_cmdline(["x" * 900])
    assert len(trimmed) <= attribution._MAX_CMDLINE


def test_scan_listening_ports_shape():
    """May legitimately be empty (macOS denies without privileges) — assert
    the shape when rows exist rather than requiring any."""
    ports = attribution.scan_listening_ports([])
    for row in ports:
        assert {"port", "proto", "pid", "app_id"} <= set(row)
        assert isinstance(row["port"], int)
