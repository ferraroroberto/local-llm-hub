"""The storage section of a capture: bounded walks, Docker degradation, storage.

The conftest stubs ``storage.collect`` for every other diagnostics test; this
module keeps the real one by reference and drives it with explicit configs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.diagnostics import report, sampler, storage, storage_report, store

REAL_COLLECT = storage.collect
REAL_DRIVES = storage._drives


@pytest.fixture(autouse=True)
def _hermetic_host(monkeypatch):
    """No test depends on this machine's drives or Docker install; the ones that
    need either re-patch ``which``/``subprocess.run`` or call ``REAL_DRIVES``."""
    monkeypatch.setattr(storage.shutil, "which", lambda name: None)
    monkeypatch.setattr(storage, "_drives", lambda deadline: {"status": "ok", "items": []})


@pytest.fixture()
def db(tmp_path):
    store.set_db_path(tmp_path / "diag.db")
    yield store
    store.set_db_path(None)


def _tree(root: Path, spec: dict) -> Path:
    """Create files from ``{relative path: size}``."""
    for rel, size in spec.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)
    return root


def _walk(path, *, budget_s=5.0, max_depth=8, max_entries=10_000, clock=time.monotonic):
    return storage.bounded_size(path, budget_s=budget_s, max_depth=max_depth,
                                max_entries=max_entries, clock=clock)


def _cfg(tmp_path, **kw) -> storage.StorageConfig:
    base = dict(roots=(), growers=(), overall_budget_s=30.0)
    base.update(kw)
    return storage.StorageConfig(**base)


# ------------------------------------------------------------------- walks


def test_walk_sums_a_tree(tmp_path):
    _tree(tmp_path, {"a": 10, "d/b": 20, "d/e/c": 30})
    assert _walk(tmp_path) == {"status": "ok", "bytes": 60}


def test_walk_of_a_missing_path_is_absent(tmp_path):
    assert _walk(tmp_path / "nope") == {"status": "absent"}


def test_walk_of_a_file_is_its_size(tmp_path):
    f = _tree(tmp_path, {"disk.vhdx": 123}) / "disk.vhdx"
    assert _walk(f) == {"status": "ok", "bytes": 123}


def test_depth_cap_is_unknown_with_a_lower_bound(tmp_path):
    _tree(tmp_path, {"a": 10, "d1/d2/d3/deep": 1000})
    result = _walk(tmp_path, max_depth=2)
    assert result["status"] == "unknown"
    assert "depth cap" in result["reason"]
    assert result["lower_bound_bytes"] == 10
    assert "bytes" not in result


def test_entry_cap_is_unknown(tmp_path):
    _tree(tmp_path, {f"f{i}": 1 for i in range(50)})
    result = _walk(tmp_path, max_entries=10)
    assert result["status"] == "unknown"
    assert "entry cap" in result["reason"]
    assert "bytes" not in result


def test_time_budget_stops_the_walk(tmp_path):
    _tree(tmp_path, {f"d{i}/f": 1 for i in range(20)})
    ticks = iter(range(10_000))
    # Every clock read advances one "second", so a 3 s budget is spent quickly.
    result = _walk(tmp_path, budget_s=3.0, clock=lambda: float(next(ticks)))
    assert result["status"] == "unknown"
    assert "time budget" in result["reason"]


def test_symlinked_directory_is_not_followed(tmp_path):
    real = _tree(tmp_path / "real", {"big": 5000})
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "small").write_bytes(b"x" * 7)
    try:
        os.symlink(real, scan / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privilege on this host")
    assert _walk(scan) == {"status": "ok", "bytes": 7}


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows reparse point")
def test_junction_is_not_followed(tmp_path):
    import _winapi
    real = _tree(tmp_path / "real", {"big": 5000})
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "small").write_bytes(b"x" * 7)
    _winapi.CreateJunction(str(real), str(scan / "junction"))
    assert _walk(scan) == {"status": "ok", "bytes": 7}


def test_unreadable_directory_makes_the_size_unknown(tmp_path, monkeypatch):
    _tree(tmp_path, {"ok/f": 5, "locked/f": 9})
    real_scandir = os.scandir

    def scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(storage.os, "scandir", scandir)
    result = _walk(tmp_path)
    assert result["status"] == "unknown"
    assert "unreadable" in result["reason"]
    assert result["lower_bound_bytes"] == 5


def test_a_walk_stuck_in_the_kernel_is_abandoned(tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(storage, "_ABANDON_GRACE_S", 0.1)
    monkeypatch.setattr(storage, "bounded_size",
                        lambda *a, cancel=None, **k: release.wait(5) and {"status": "ok", "bytes": 1})
    cfg = _cfg(tmp_path, walk_budget_s=0.2)
    started = time.monotonic()
    result = storage._measure(tmp_path, cfg, storage._Deadline(30, time.monotonic), time.monotonic)
    release.set()
    assert result["status"] == "unknown"
    assert time.monotonic() - started < 3


# -------------------------------------------------------------------- docker


def _fake_docker(monkeypatch, responses):
    """``responses`` maps a docker subcommand to stdout, an exception or an exit code."""
    monkeypatch.setattr(storage.shutil, "which", lambda name: "docker" if name == "docker" else None)

    def run(cmd, **kw):
        action = responses[cmd[1]]
        if isinstance(action, Exception):
            raise action
        if isinstance(action, int):
            return subprocess.CompletedProcess(cmd, action, b"", b"Cannot connect to the daemon")
        return subprocess.CompletedProcess(cmd, 0, action.encode(), b"")

    monkeypatch.setattr(storage.subprocess, "run", run)


_PS = ('{"Names":"web","State":"running","Status":"Up 2 hours (healthy)"}\n'
       '{"Names":"db","State":"exited","Status":"Exited (0) 3 days ago"}\n')
_DF = '{"Type":"Images","TotalCount":"4","Active":"2","Size":"5.1GB","Reclaimable":"2GB (39%)"}\n'


def _collect(tmp_path, **kw):
    return REAL_COLLECT(_cfg(tmp_path, **kw))


def test_docker_reports_containers_and_usage(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, {"info": "27.0.1\n", "ps": _PS, "system": _DF})
    docker = _collect(tmp_path)["docker"]
    assert docker["status"] == "ok" and docker["server_version"] == "27.0.1"
    assert [(c["name"], c["state"], c["health"]) for c in docker["containers"]["items"]] == [
        ("web", "running", "healthy"), ("db", "exited", None)]
    assert docker["usage"]["items"][0]["size"] == "5.1GB"


def test_missing_docker_cli_is_unknown_and_the_rest_completes(tmp_path):
    _tree(tmp_path / "g", {"f": 11})
    section = _collect(tmp_path, growers=(("cache", tmp_path / "g"),))
    assert section["docker"] == storage.unknown("docker CLI not on PATH")
    assert section["growers"]["items"][0]["bytes"] == 11
    assert section["drives"]["status"] == "ok"


def test_drives_report_total_and_free_bytes():
    section = REAL_DRIVES(storage._Deadline(30, time.monotonic))
    assert section["status"] == "ok" and section["items"]
    ok = [d for d in section["items"] if d["status"] == "ok"]
    assert ok and all(0 <= d["free_bytes"] <= d["total_bytes"] for d in ok)


def test_docker_info_timeout_is_unknown(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, {"info": subprocess.TimeoutExpired("docker", 1)})
    docker = _collect(tmp_path)["docker"]
    assert docker["status"] == "unknown" and "timed out" in docker["reason"]


def test_daemon_down_is_unknown_with_the_reason(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, {"info": 1})
    docker = _collect(tmp_path)["docker"]
    assert docker["status"] == "unknown" and "daemon" in docker["reason"]


def test_system_df_timeout_keeps_the_container_list(tmp_path, monkeypatch):
    _fake_docker(monkeypatch, {"info": "27\n", "ps": _PS,
                               "system": subprocess.TimeoutExpired("docker", 1)})
    docker = _collect(tmp_path)["docker"]
    assert docker["status"] == "ok"
    assert len(docker["containers"]["items"]) == 2
    assert docker["usage"]["status"] == "unknown" and "timed out" in docker["usage"]["reason"]


# ------------------------------------------------------------ sections / config


def test_top_folders_rank_by_size_and_keep_unknown_labelled(tmp_path):
    root = _tree(tmp_path / "root", {"small/f": 5, "big/f": 500, "deep/f": 50, "deep/a/b/c/f": 50})
    section = _collect(tmp_path, roots=(root,), max_depth=2, top_n=5)["top_folders"]
    folders = section["roots"][0]["folders"]
    assert [Path(f["path"]).name for f in folders] == ["big", "deep", "small"]
    assert folders[0] == {"path": str(root / "big"), "status": "ok", "bytes": 500}
    assert folders[1]["status"] == "unknown" and "bytes" not in folders[1]
    assert section["roots"][0]["unmeasured"] == 1


def test_overall_budget_makes_later_fields_unknown(tmp_path):
    _tree(tmp_path / "g", {"f": 1})
    section = _collect(tmp_path, growers=(("cache", tmp_path / "g"),), overall_budget_s=0.0)
    assert section["growers"]["items"][0]["status"] == "unknown"
    assert "budget" in section["growers"]["items"][0]["reason"]


def test_a_failing_section_does_not_take_the_others_down(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_drives", lambda deadline: 1 / 0)
    section = _collect(tmp_path)
    assert section["drives"]["status"] == "unknown"
    assert section["growers"]["status"] == "ok"


def test_local_config_overrides_and_extends_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_STORAGE_ROOT", str(tmp_path))
    cfg_file = tmp_path / "storage.json"
    cfg_file.write_text('{"roots": ["%TEST_STORAGE_ROOT%/r", "$TEST_STORAGE_ROOT/s"],'
                        ' "growers": [{"label": "logs", "path": "~/logs"}, {"label": "hub-models", "path": "m"}],'
                        ' "walk_budget_s": 999999, "top_n": "bad"}', encoding="utf-8")
    cfg = storage.load_config(cfg_file)
    labels = dict(cfg.growers)
    assert "logs" in labels and "huggingface-cache" in labels
    assert labels["hub-models"] == Path("m")
    assert cfg.walk_budget_s == 60 and cfg.top_n == storage.StorageConfig.top_n
    assert len(cfg.roots) == 2
    if sys.platform == "win32":
        assert cfg.roots[0] == tmp_path / "r"


def test_missing_or_broken_config_gives_the_defaults(tmp_path):
    assert storage.load_config(tmp_path / "none.json").roots == (Path.home(),)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert storage.load_config(bad).roots == (Path.home(),)


# ----------------------------------------------------- store / sampler / report


def _section():
    return {"schema": 1, "drives": {"status": "ok", "items": [
        {"mount": "C", "fstype": "NTFS", "status": "ok", "total_bytes": 2 * 1024 ** 3,
         "free_bytes": 1024 ** 3}]},
        "docker": storage.unknown("docker CLI not on PATH"),
        "growers": {"status": "ok", "items": [
            {"label": "cache", "path": "x", "status": "unknown", "reason": "depth cap 2 reached",
             "lower_bound_bytes": 1024 ** 3}]},
        "top_folders": {"status": "ok", "roots": []}}


def test_schema_migrates_from_v2_and_round_trips(tmp_path, db):
    import sqlite3
    path = tmp_path / "old.db"
    store.set_db_path(path)
    run_id = db.create_run(machine_id="m", os_name="T", interval_s=0, duration_s=0)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE runs DROP COLUMN storage_json")
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()
    assert db.get_run(run_id)["storage"] == {}          # migration re-adds the column
    db.save_storage(run_id, _section())
    assert db.get_run(run_id)["storage"]["docker"]["status"] == "unknown"
    assert "storage" not in db.list_runs()[0]


def test_summary_and_report_carry_the_storage_section(db):
    run_id = db.create_run(machine_id="m", os_name="T", interval_s=0, duration_s=0)
    db.finish_run(run_id)
    db.save_storage(run_id, _section())
    assert report.summary(run_id)["storage"]["schema"] == 1
    text = report.markdown_report(run_id)
    assert "## Storage" in text
    assert "docker CLI not on PATH" in text
    assert "at least 1.0 GiB" in text and "depth cap" in text


def test_report_says_so_for_a_run_without_storage(db):
    run_id = db.create_run(machine_id="m", os_name="T", interval_s=0, duration_s=0)
    db.finish_run(run_id)
    assert "No storage section was recorded" in report.markdown_report(run_id)
    assert storage_report.markdown_lines({})[-2].startswith("_No storage section")


def test_one_shot_stores_the_section(db, monkeypatch):
    import asyncio
    monkeypatch.setattr(sampler.attribution, "prime_cpu_percent", lambda: None)
    run_id = asyncio.run(sampler.one_shot())["run_id"]
    assert db.get_run(run_id)["storage"]["schema"] == storage.STORAGE_SCHEMA


def test_a_storage_exception_leaves_the_run_complete(db, monkeypatch):
    import asyncio
    monkeypatch.setattr(sampler.attribution, "prime_cpu_percent", lambda: None)

    def boom(cfg=None, **kw):
        raise RuntimeError("walk exploded")

    monkeypatch.setattr(storage, "collect", boom)
    run_id = asyncio.run(sampler.one_shot())["run_id"]
    run = db.get_run(run_id)
    assert run["status"] == "complete"
    assert run["storage"]["status"] == "unknown" and "walk exploded" in run["storage"]["reason"]
