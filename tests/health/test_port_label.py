"""Port watch labels: an interpreter is named by its repo and entry point, never by a path.

Every test drives a fake process and a temp folder tree, so no real process is read.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import psutil
import pytest

from src.health import port_label, port_probe, port_rules
from src.health.port_window import PortWindow

T0 = 1_800_000_000.0


def _repo(tmp_path: Path, name: str = "fake-repo", *, git_file: bool = False) -> Path:
    root = tmp_path / name
    (root / "src" / "deep").mkdir(parents=True)
    if git_file:  # a linked worktree keeps a .git file, not a folder
        (root / ".git").write_text("gitdir: elsewhere")
    else:
        (root / ".git").mkdir()
    return root


def _process(cwd: Optional[Path], cmdline: List[str], *, cwd_error: Optional[Exception] = None):
    def read_cwd() -> str:
        if cwd_error:
            raise cwd_error
        return str(cwd)

    return lambda pid: SimpleNamespace(cwd=read_cwd, cmdline=lambda: cmdline)


def test_a_module_run_is_labelled_by_its_repo_and_module(tmp_path):
    root = _repo(tmp_path)
    open_process = _process(root / "src" / "deep", ["C:\\py\\python.exe", "-u", "-m", "pytest", "-q", "tests"])
    assert port_label.process_label(7, "python.exe", open_process) == "fake-repo: -m pytest"


def test_a_script_run_is_labelled_by_its_basename_only(tmp_path):
    root = _repo(tmp_path)
    open_process = _process(root, ["python.exe", "-X", "utf8", "C:\\work\\build\\server.py", "--token", "s3cret"])
    assert port_label.process_label(7, "Python.EXE", open_process) == "fake-repo: server.py"
    posix = _process(root, ["node", "/srv/app/index.js", "--port", "80"])
    assert port_label.process_label(7, "node.exe", posix) == "fake-repo: index.js"


def test_a_linked_worktree_counts_as_a_repo(tmp_path):
    root = _repo(tmp_path, "fake-repo-wt-9", git_file=True)
    assert port_label.process_label(7, "pythonw.exe", _process(root / "src", ["pythonw.exe", "-m", "tray"])) \
        == "fake-repo-wt-9: -m tray"


def test_a_folder_outside_any_repo_keeps_the_entry_point_alone(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    assert port_label.process_label(7, "python.exe", _process(loose, ["python.exe", "tool.py"])) == "tool.py"


def test_an_inline_script_never_stores_its_code(tmp_path):
    root = _repo(tmp_path)
    label = port_label.process_label(7, "python.exe", _process(root, ["python.exe", "-c", "import os; print('k')"]))
    assert label == "fake-repo: -c"


def test_a_process_that_is_not_an_interpreter_has_no_label(tmp_path):
    root = _repo(tmp_path)
    assert port_label.process_label(7, "svchost.exe", _process(root, ["svchost.exe", "-k", "netsvcs"])) is None


@pytest.mark.parametrize("error", [psutil.AccessDenied(7), psutil.NoSuchProcess(7), OSError("gone")])
def test_a_process_that_cannot_be_read_has_label_none_not_an_empty_string(error, tmp_path):
    open_process = _process(None, ["python.exe", "-m", "pytest"], cwd_error=error)
    assert port_label.process_label(7, "python.exe", open_process) is None


def test_a_process_that_cannot_be_opened_has_label_none():
    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    assert port_label.process_label(7, "python.exe", gone) is None


def test_no_label_holds_a_path_separator(tmp_path):
    root = _repo(tmp_path)
    for cmdline in (["python.exe", "-m", "a/b\\c"], ["python.exe", "C:\\x\\y\\run.py"], ["node", "../up/z.js"]):
        label = port_label.process_label(7, "python.exe", _process(root, cmdline))
        assert label and "/" not in label and "\\" not in label


def _tally(ts: float, pids) -> dict:
    return {"ts": ts, "probes": {"tcp": "ok", "udp": "ok"}, "counts": {pid: (5, 0) for pid in pids}}


def test_the_window_looks_a_label_up_once_per_pid_across_tallies():
    calls: List[int] = []

    def label_of(pid: int) -> Optional[str]:
        calls.append(pid)
        return f"repo-{pid}: -m x" if pid != 2 else None

    window = PortWindow()
    window.add(_tally(T0, [1, 2]), lambda pid: f"p{pid}.exe", 60, label_of)
    window.add(_tally(T0 + 1, [1, 2]), lambda pid: f"p{pid}.exe", 60, label_of)
    assert sorted(calls) == [1, 2]  # a second tally of the same pids reads nothing again
    window.add(_tally(T0 + 2, [1, 2, 3]), lambda pid: f"p{pid}.exe", 60, label_of)
    assert sorted(calls) == [1, 2, 3]
    out = window.peaks("tcp", event_ts=T0, top_n=5, range_size=100, confirm_percent=25)
    rows = {r["pid"]: r for r in out["holders"]}
    assert rows[1]["label"] == "repo-1: -m x" and rows[2]["label"] is None and rows[3]["label"] == "repo-3: -m x"


def test_a_pid_that_ages_out_of_the_window_is_looked_up_again_if_it_returns():
    calls: List[int] = []
    window = PortWindow()

    def label_of(pid: int) -> Optional[str]:
        calls.append(pid)
        return "r: a.py"

    window.add(_tally(T0, [1]), str, 5, label_of)
    window.add(_tally(T0 + 10, [2]), str, 5, label_of)  # pid 1 has aged out
    window.add(_tally(T0 + 11, [1]), str, 5, label_of)
    assert calls == [1, 2, 1]


def test_a_probe_with_a_fake_name_lookup_reads_no_live_process():
    probe = port_probe.PortProbe(lambda protocol: [], {1: "x.exe"}.get, {"tcp": (1, 2), "udp": (1, 2)})
    assert probe.label_of(1) is None


def test_a_sample_holder_carries_its_label():
    table = [SimpleNamespace(pid=1, laddr=SimpleNamespace(port=50_000), status=psutil.CONN_ESTABLISHED, raddr=())]
    probe = port_probe.PortProbe(lambda protocol: table, {1: "python.exe"}.get, {"tcp": (50_000, 50_099),
                                 "udp": (50_000, 50_099)}, label_of=lambda pid, name: "r: -m pytest")
    (holder,) = probe.sample(top_n=3, now=T0)["holders"]
    assert holder["label"] == "r: -m pytest"


def test_the_event_text_shows_the_label_beside_the_name():
    peak = {"protocol": "tcp", "confirmed": True, "from": T0 - 59, "to": T0, "samples": 60, "min_rise": 10,
            "holders": [{"pid": 1, "name": "python.exe", "label": "r: -m pytest", "peak": 83, "at": T0, "low": 5},
                        {"pid": 2, "name": "python.exe", "label": None, "peak": 60, "at": T0, "low": 0}]}
    text = port_rules.peak_text({"peak": peak})
    assert "python.exe (r: -m pytest) 83" in text and "python.exe 60" in text
    snapshot = {"delay_s": 9, "probes": {"tcp": "ok"}, "ports": 1,
                "holders": [{"pid": 1, "name": "python.exe", "label": "r: -m pytest", "tcp": 83, "udp": 0,
                             "ports": 83}]}
    assert "python.exe (r: -m pytest) (83 TCP ports)" in port_rules.snapshot_text(
        {"snapshot": snapshot, "protocol": "tcp"})


def test_a_top_holder_row_carries_its_label():
    sample = {"ts": T0, "holders": [{"pid": 1, "name": "python.exe", "label": "r: -m pytest", "tcp": 3, "udp": 0,
                                     "ports": 3}]}
    (row,) = port_rules.offenders([sample], T0)
    assert row["label"] == "r: -m pytest" and row["name"] == "python.exe"
