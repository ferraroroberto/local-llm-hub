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


@pytest.fixture(autouse=True)
def _reset_platform():
    """No test may leak a platform override into the next one — the whole
    suite would then silently assert against the wrong OS table."""
    yield
    attribution.set_platform(None)


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
    the shape when rows exist rather than requiring any. Returns
    ``(rows, denied)`` so a blind scan is distinguishable from an empty one."""
    ports, denied = attribution.scan_listening_ports([])
    assert isinstance(denied, bool)
    for row in ports:
        assert {"port", "proto", "pid", "app_id"} <= set(row)
        assert isinstance(row["port"], int)
    if denied:
        assert ports == []   # a denied scan yields no rows, by definition


# ------------------------------------------------ per-OS rules + path prefixes (#320)
#
# Before this, `config/diagnostics_apps.json` was tuned on the Windows box and
# macOS captures came back 99% `unattributed` (565 of 570 groups). These tests
# run against the *real committed config* under a forced platform, because the
# thing that was broken was the shipped data, not the engine — a synthetic
# fixture would have kept passing throughout the bug.


@pytest.fixture()
def darwin():
    attribution.set_rules_path(None)
    attribution.set_platform("darwin")


@pytest.fixture()
def linux():
    attribution.set_rules_path(None)
    attribution.set_platform("linux")


@pytest.fixture()
def windows():
    attribution.set_rules_path(None)
    attribution.set_platform("windows")


def test_apple_daemons_attribute_by_path(darwin):
    """The macOS inventory is overwhelmingly Apple daemons whose names mean
    nothing on their own; the path is what identifies them."""
    for cmd in (
        "/usr/libexec/secd",
        "/System/Library/PrivateFrameworks/CloudKitDaemon.framework/Support/cloudd",
        "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder",
        "/System/Applications/Weather.app/Contents/MacOS/Weather",
        "/System/Cryptexes/OS/usr/libexec/foo",
        "/usr/sbin/systemstats",
        "/Library/Apple/System/Library/CoreServices/XProtect.app/Contents/MacOS/XProtect",
    ):
        name = cmd.rsplit("/", 1)[-1]
        assert attribution.attribute(name, cmd) == "macos-system", cmd


def test_truncated_daemon_name_still_attributes_via_its_path(darwin):
    """macOS reports a 16-char kernel name for processes whose command line the
    hub may not read ('AppleCredentialM'). The path carries the real signal —
    which is why the scan falls back to `exe`."""
    exe = ("/System/Library/PrivateFrameworks/AppleNeuralEngine.framework"
           "/XPCServices/ANECompilerService.xpc/Contents/MacOS/ANECompilerService")
    assert attribution.attribute("ANECompilerServi", exe) == "macos-system"


def test_macos_control_center_is_not_elgato(darwin):
    """The live mis-attribution this issue found: Elgato ships a Windows app
    called Control Center, so a name-only rule claimed Apple's macOS shell
    component for it on every Mac capture."""
    cmd = "/System/Library/CoreServices/ControlCenter.app/Contents/MacOS/ControlCenter"
    assert attribution.attribute("ControlCenter", cmd) == "macos-system"


def test_windows_control_center_is_still_elgato(windows):
    """...and the Windows mapping must survive the fix."""
    assert attribution.attribute(
        "ControlCenter.exe", r"C:\Program Files\Elgato\ControlCenter.exe") == "elgato"


def test_user_installed_macos_software_stays_reviewable(darwin):
    """`unattributed` is the review list — the entire point of the capture is
    to surface user-installed bloat. Bucketing /Applications or Homebrew would
    hide exactly the answer the user is looking for."""
    assert attribution.attribute(
        "SomeApp", "/Applications/SomeApp.app/Contents/MacOS/SomeApp") == "unattributed"
    assert attribution.attribute(
        "mystery", "/opt/homebrew/bin/mystery --serve") == "unattributed"


def test_linux_usr_bin_is_not_bucketed_as_system(linux):
    """The reason the rule tables are per-OS at all: /usr/bin is Apple-owned
    and SIP-protected on macOS, but is where ordinary user software lives on
    Linux. One shared table cannot be correct for both."""
    assert attribution.attribute("mystery", "/usr/bin/mystery --serve") == "unattributed"
    assert attribution.attribute("systemd-logind", "/usr/lib/systemd/systemd-logind") == "linux-system"


def test_macos_usr_bin_is_bucketed_as_system(darwin):
    assert attribution.attribute("mystery", "/usr/bin/mystery") == "macos-system"


def test_the_macs_own_hub_is_attributed(darwin):
    """mac-mini-m4 runs the hub from a Homebrew interpreter outside any fleet
    root, so only the module rule can catch it."""
    cmd = ("/opt/homebrew/Cellar/python@3.12/3.12.13_4/Frameworks/Python.framework"
           "/Versions/3.12/Resources/Python.app/Contents/MacOS/Python -m src.server")
    assert attribution.attribute("Python", cmd) == "local-llm-hub"


def test_binaries_still_win_over_path_prefixes(darwin):
    """Path rules are the broad net and must stay last: a Homebrew-installed
    llama-server is llama.cpp, not a system daemon."""
    assert attribution.attribute("llama-server", "/usr/local/bin/llama-server --port 8088") == "llama.cpp"


def test_fleet_root_still_wins_on_every_platform(darwin):
    cmd = "/opt/automation/photo-ocr/.venv/bin/python -m src"
    assert attribution.attribute("python3", cmd) == "photo-ocr"


def test_platform_groups_do_not_leak_across_platforms():
    """A `_darwin` rule must be invisible on Windows and vice versa."""
    attribution.set_rules_path(None)
    attribution.set_platform("windows")
    assert attribution.attribute("secd", "/usr/libexec/secd") != "macos-system"
    attribution.set_platform("darwin")
    assert attribution.attribute("secd", "/usr/libexec/secd") == "macos-system"


def test_longest_path_prefix_wins(tmp_path):
    """Overlapping prefixes must resolve by specificity, not file order."""
    path = tmp_path / "apps.json"
    path.write_text(json.dumps({
        "path_prefixes": {"/system/": "broad", "/system/library/": "specific"},
    }), encoding="utf-8")
    attribution.set_rules_path(path)
    try:
        assert attribution.attribute("x", "/System/Library/foo") == "specific"
        assert attribution.attribute("x", "/System/other/foo") == "broad"
    finally:
        attribution.set_rules_path(None)


def test_platform_specific_group_extends_and_overrides(tmp_path):
    path = tmp_path / "apps.json"
    path.write_text(json.dumps({
        "fleet_roots": ["/shared"],
        "fleet_roots_darwin": ["/mac-only"],
        "binaries": {"foo": "shared-foo", "bar": "bar"},
        "binaries_darwin": {"foo": "mac-foo"},
    }), encoding="utf-8")
    attribution.set_rules_path(path)
    attribution.set_platform("darwin")
    try:
        assert attribution.attribute("foo", "foo") == "mac-foo"      # overridden
        assert attribution.attribute("bar", "bar") == "bar"          # untouched
        assert attribution.attribute("p", "/mac-only/repo-x/p") == "repo-x"
        assert attribution.attribute("p", "/shared/repo-y/p") == "repo-y"
    finally:
        attribution.set_rules_path(None)


def test_scan_falls_back_to_exe_when_cmdline_is_unreadable():
    """On macOS the hub cannot read another user's command line, so 310 of 673
    processes report an empty cmdline. `exe` uses a different kernel call that
    stays readable and is what makes those rows attributable at all."""
    rows = attribution.scan_processes()
    assert rows
    assert all("cmdline" in r for r in rows)


def test_path_prefix_is_anchored_not_a_substring(tmp_path):
    """`/bin/` as a substring also matches `/opt/homebrew/bin/`. Matching
    anywhere would quietly file user-installed software as OS-owned, which is
    the opposite of what the bucket means."""
    path = tmp_path / "apps.json"
    path.write_text(json.dumps({"path_prefixes": {"/bin/": "sys"}}), encoding="utf-8")
    attribution.set_rules_path(path)
    try:
        assert attribution.attribute("a", "/bin/ls") == "sys"
        assert attribution.attribute("b", "/opt/homebrew/bin/mystery") == "unattributed"
    finally:
        attribution.set_rules_path(None)


def test_quoted_windows_path_still_matches(windows):
    """Windows command lines commonly arrive quoted; the anchor must see past it."""
    assert attribution.attribute(
        "svchost.exe", r'"C:\Windows\system32\svchost.exe" -k netsvcs') == "windows-services"
