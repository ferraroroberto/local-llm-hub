"""Diagnostics SQLite store — schema, writes, rollups, retention (#315)."""

from __future__ import annotations

import time

import pytest

from src.diagnostics import store


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Point the store at a throwaway DB for each test."""
    store.set_db_path(tmp_path / "diag.db")
    yield store
    store.set_db_path(None)


def _sample(ts: float, *, cpu=10.0, ram=50.0, procs=3) -> store.SystemSample:
    return store.SystemSample(
        ts=ts, cpu_percent=cpu, per_core=[cpu, cpu], load_avg=None,
        ram={"used_gb": 8.0, "total_gb": 16.0, "percent": ram},
        swap={"used_gb": 0.5, "total_gb": 8.0, "percent": 6.0},
        disk={"used_gb": 100.0, "total_gb": 500.0, "percent": 20.0},
        disk_io={"read_bytes": 1}, net_io={"bytes_sent": 2}, gpus=[],
        process_count=procs,
    )


def _proc(pid, name, app_id, rss_mb, cpu=1.0, cmdline=None):
    return {
        "pid": pid, "ppid": 1, "name": name, "cmdline": cmdline or f"/bin/{name}",
        "app_id": app_id, "cpu_percent": cpu, "rss_bytes": int(rss_mb * 1024 ** 2),
        "num_threads": 4, "status": "running", "create_time": 1.0,
    }


def _new_run(db, **kw):
    params = {"machine_id": "pc-test", "os_name": "TestOS", "interval_s": 15.0,
              "duration_s": 60.0}
    params.update(kw)
    return db.create_run(**params)


def test_schema_is_created_and_versioned(db):
    with db.connect() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert version == store.SCHEMA_VERSION
    assert {"runs", "samples", "process_samples", "ports", "verdicts"} <= tables


def test_migration_is_idempotent(db):
    """Connecting repeatedly must not re-run the ladder or lose data."""
    run_id = _new_run(db)
    for _ in range(3):
        with db.connect():
            pass
    assert db.get_run(run_id) is not None


def test_write_and_read_back_a_sample(db):
    run_id = _new_run(db)
    ts = time.time()
    db.write_sample(
        run_id, _sample(ts),
        [_proc(1, "python.exe", "local-llm-hub", 100)],
        [{"port": 8000, "proto": "tcp", "pid": 1, "name": "python.exe",
          "app_id": "local-llm-hub"}],
    )
    rows = db.samples(run_id)
    assert len(rows) == 1
    assert rows[0]["cpu_percent"] == 10.0
    assert rows[0]["per_core"] == [10.0, 10.0]   # JSON column decoded
    ports = db.listening_ports(run_id)
    assert ports[0]["port"] == 8000 and ports[0]["app_id"] == "local-llm-hub"


def test_app_aggregates_group_by_app_not_pid(db):
    """Three PIDs of one app roll up to one row with peak_procs=3 — the
    'app-launcher: 3 procs' headline the feature exists for."""
    run_id = _new_run(db)
    ts = time.time()
    db.write_sample(run_id, _sample(ts), [
        _proc(1, "python.exe", "app-launcher", 100),
        _proc(2, "python.exe", "app-launcher", 200),
        _proc(3, "python.exe", "app-launcher", 300),
        _proc(4, "llama-server", "llama.cpp", 50),
    ], [])
    apps = {a["app_id"]: a for a in db.app_aggregates(run_id)}
    assert apps["app-launcher"]["peak_procs"] == 3
    assert apps["app-launcher"]["peak_rss"] == pytest.approx(600 * 1024 ** 2)
    assert apps["llama.cpp"]["peak_procs"] == 1


def test_peak_procs_is_per_tick_not_whole_run(db):
    """A process that restarts (new PID) between ticks must not inflate the
    app's apparent concurrency — peak_procs is the largest *per-tick* count."""
    run_id = _new_run(db)
    now = time.time()
    db.write_sample(run_id, _sample(now), [_proc(1, "python.exe", "app-launcher", 100)], [])
    db.write_sample(run_id, _sample(now + 15), [_proc(2, "python.exe", "app-launcher", 100)], [])
    apps = {a["app_id"]: a for a in db.app_aggregates(run_id)}
    assert apps["app-launcher"]["peak_procs"] == 1


def test_process_count_timeline_is_per_tick_per_app(db):
    run_id = _new_run(db)
    now = time.time()
    db.write_sample(run_id, _sample(now), [
        _proc(1, "python.exe", "app-launcher", 10),
        _proc(2, "python.exe", "app-launcher", 10),
    ], [])
    db.write_sample(run_id, _sample(now + 15), [_proc(1, "python.exe", "app-launcher", 10)], [])
    timeline = db.process_count_timeline(run_id)
    assert [r["procs"] for r in timeline] == [2, 1]


def test_finish_and_list_runs(db):
    run_id = _new_run(db)
    db.finish_run(run_id)
    runs = db.list_runs()
    assert runs[0]["run_id"] == run_id
    assert runs[0]["status"] == "complete"
    assert runs[0]["ended_at"] is not None


def test_close_orphan_runs_marks_interrupted(db):
    """A run left 'running' by a crashed hub must not look live forever."""
    run_id = _new_run(db)
    assert db.close_orphan_runs() == 1
    run = db.get_run(run_id)
    assert run["status"] == "interrupted" and run["ended_at"] is not None
    assert db.close_orphan_runs() == 0     # idempotent


def test_baseline_is_exclusive_per_machine(db):
    first = _new_run(db)
    second = _new_run(db)
    db.set_baseline(first)
    db.set_baseline(second)
    assert db.baseline_run("pc-test")["run_id"] == second
    assert db.get_run(first)["is_baseline"] is False


def test_set_baseline_rejects_unknown_run(db):
    with pytest.raises(KeyError):
        db.set_baseline("nope")


def test_delete_run_cascades(db):
    run_id = _new_run(db)
    db.write_sample(run_id, _sample(time.time()), [_proc(1, "p", "a", 10)],
                    [{"port": 1, "proto": "tcp", "pid": 1, "name": "p", "app_id": "a"}])
    db.delete_run(run_id)
    assert db.get_run(run_id) is None
    assert db.samples(run_id) == []
    assert db.process_aggregates(run_id) == []
    assert db.listening_ports(run_id) == []


def test_prune_drops_raw_rows_but_keeps_run_metadata(db):
    run_id = _new_run(db)
    old = time.time() - 200 * 86400
    db.write_sample(run_id, _sample(old), [_proc(1, "p", "a", 10)], [])
    with db.connect() as conn:
        conn.execute("UPDATE runs SET started_at = ? WHERE run_id = ?", (old, run_id))

    result = db.prune(retention_days=90)
    assert result["runs_pruned"] == 1
    assert db.samples(run_id) == []          # bulk rows gone
    run = db.get_run(run_id)
    assert run is not None and run["status"] == "pruned"   # metadata kept


def test_prune_spares_a_baseline(db):
    """Baselines are the comparison anchor — retention must never eat one."""
    run_id = _new_run(db)
    old = time.time() - 200 * 86400
    db.write_sample(run_id, _sample(old), [_proc(1, "p", "a", 10)], [])
    with db.connect() as conn:
        conn.execute("UPDATE runs SET started_at = ? WHERE run_id = ?", (old, run_id))
    db.set_baseline(run_id)

    assert db.prune(retention_days=90)["runs_pruned"] == 0
    assert len(db.samples(run_id)) == 1


def test_save_and_read_verdict(db):
    run_id = _new_run(db)
    db.save_verdict(run_id, "warning", [{"rule": "cpu.sustained", "level": "warning"}])
    run = db.get_run(run_id)
    assert run["verdict_level"] == "warning"
    assert run["findings"][0]["rule"] == "cpu.sustained"
