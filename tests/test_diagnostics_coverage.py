"""Per-collector coverage — 'unmeasured must never read as measured' (#322).

The defect these pin: on macOS the ports scan and ~42% of per-process memory/CPU
reads are denied by privilege, and every one of those degraded to an empty/zero
result that looked identical to a genuine one — so a blind run reported
``HEALTHY`` with a vanished ports section. These tests assert that blind is now
distinguishable from empty at every layer: store, the coverage engine, the
verdict rules, and the rendered report.
"""

from __future__ import annotations

import time

import pytest

from src.diagnostics import coverage, report, rules, store


@pytest.fixture()
def db(tmp_path):
    store.set_db_path(tmp_path / "diag.db")
    rules.set_rules_path(None)
    rules.reload_thresholds()
    yield store
    store.set_db_path(None)


def _sample(ts, *, procs=3):
    return store.SystemSample(
        ts=ts, cpu_percent=5.0, per_core=[5.0], load_avg=None,
        ram={"used_gb": 4.0, "total_gb": 16.0, "percent": 30.0},
        swap={"used_gb": 0.0, "total_gb": 8.0, "percent": 0.0},
        disk={"used_gb": 100.0, "total_gb": 500.0, "percent": 20.0},
        disk_io={}, net_io={}, gpus=[], process_count=procs,
    )


def _proc(pid, *, rss_mb=10, cpu=1.0):
    """A readable process. rss_mb=None / cpu=None models an unreadable one —
    psutil stores NULL (never 0) when denied, which is what coverage keys on."""
    return {
        "pid": pid, "ppid": 1, "name": f"p{pid}", "cmdline": f"/bin/p{pid}",
        "app_id": "unattributed", "num_threads": 2, "status": "running",
        "create_time": 1.0,
        "cpu_percent": cpu,
        "rss_bytes": int(rss_mb * 1024 ** 2) if rss_mb is not None else None,
    }


def _run(db, processes):
    run_id = db.create_run(machine_id="pc-test", os_name="TestOS",
                           interval_s=15.0, duration_s=60.0)
    db.write_sample(run_id, _sample(time.time()), processes, [])
    db.finish_run(run_id)
    return run_id


# ------------------------------------------------------------------- store

def test_schema_v2_adds_coverage_column(db):
    assert store.SCHEMA_VERSION >= 2
    with db.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "coverage_json" in cols


def test_concurrent_first_open_migrates_safely(tmp_path):
    """Several connections opening a fresh DB at once must all succeed. This
    guards two distinct concurrent-first-open failures that both surfaced as
    intermittent gate flakes: the v2 ``ALTER TABLE ADD COLUMN`` racing itself
    ('duplicate column', #322), and the journal-mode change racing itself
    ('database is locked' — SQLite skips the busy handler for it, #326)."""
    import threading

    store.set_db_path(tmp_path / "race.db")
    errors: list = []
    barrier = threading.Barrier(8)

    def _open():
        try:
            barrier.wait()          # line every thread up on the very first open
            with store.connect() as conn:
                conn.execute("SELECT coverage_json FROM runs LIMIT 1").fetchall()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_open) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.set_db_path(None)
    assert not errors, f"concurrent migration raised: {errors}"


def test_coverage_round_trips(db):
    run_id = _run(db, [_proc(1)])
    db.save_coverage(run_id, {"ports": {"status": "denied"}})
    assert db.get_run(run_id)["coverage"] == {"ports": {"status": "denied"}}


def test_a_run_without_coverage_reads_back_empty_not_broken(db):
    """A v1-era run (never had coverage written) must degrade to an empty map,
    not raise."""
    run_id = _run(db, [_proc(1)])
    assert db.get_run(run_id)["coverage"] == {}


def test_proc_readability_counts_null_reads_by_distinct_pid(db):
    run_id = _run(db, [_proc(1), _proc(2), _proc(3, rss_mb=None, cpu=None)])
    read = db.proc_readability(run_id)
    assert read == {"total": 3, "mem_ok": 2, "cpu_ok": 2}


def test_a_genuine_zero_rss_is_readable_not_denied(db):
    """0 bytes RSS is a real reading (a kernel thread); only NULL means denied.
    Conflating them is the bug — assert the store keeps them distinct."""
    run_id = _run(db, [_proc(1, rss_mb=0, cpu=0.0)])
    assert db.proc_readability(run_id) == {"total": 1, "mem_ok": 1, "cpu_ok": 1}


# ---------------------------------------------------------------- engine

def test_compute_flags_denied_ports_and_partial_memory(db):
    run_id = _run(db, [_proc(1), _proc(2, rss_mb=None, cpu=None)])
    cov = coverage.compute(run_id, ports_denied=True)
    assert cov["ports"]["status"] == "denied"
    assert cov["proc_mem"]["status"] == "partial"
    assert cov["proc_mem"] == {"status": "partial", "readable": 1, "total": 2}
    assert coverage.is_degraded(cov)
    assert set(coverage.blind_collectors(cov)) == {"ports", "proc_mem", "proc_cpu"}


def test_compute_is_clean_when_everything_readable(db):
    run_id = _run(db, [_proc(1), _proc(2)])
    cov = coverage.compute(run_id, ports_denied=False)
    assert cov["ports"]["status"] == "ok"
    assert cov["proc_mem"]["status"] == "ok"
    assert not coverage.is_degraded(cov)
    assert coverage.blind_collectors(cov) == []


def test_apple_silicon_marks_gpu_unsupported(db, monkeypatch):
    """Unified memory has no discrete VRAM figure — a known structural gap,
    recorded as `unsupported` rather than silently absent."""
    run_id = _run(db, [_proc(1)])
    monkeypatch.setattr(coverage.sys, "platform", "darwin")
    cov = coverage.compute(run_id, ports_denied=False)
    assert cov["gpu"]["status"] == "unsupported"
    # `unsupported` is a known gap, not a blind collector — it must not force
    # the "partial coverage" qualifier on its own.
    assert not coverage.is_degraded(cov)


def test_non_darwin_omits_gpu_coverage(db, monkeypatch):
    run_id = _run(db, [_proc(1)])
    monkeypatch.setattr(coverage.sys, "platform", "win32")
    assert "gpu" not in coverage.compute(run_id, ports_denied=False)


# ------------------------------------------------------------------ rules

def test_denied_collector_reports_not_evaluated_without_escalating(db):
    """A rule that could not run must say so — silence is indistinguishable
    from a pass — but 'we couldn't look' is not 'something is wrong', so the
    health level stays healthy."""
    run_id = _run(db, [_proc(1)])
    db.save_coverage(run_id, {"ports": {"status": "denied"}})
    result = rules.evaluate(run_id)
    rules_fired = {f["rule"] for f in result["findings"]}
    assert "ports.not_evaluated" in rules_fired
    assert result["level"] == "healthy"
    note = next(f for f in result["findings"] if f["rule"] == "ports.not_evaluated")
    assert note["level"] == "info"


def test_ok_ports_coverage_raises_no_note(db):
    run_id = _run(db, [_proc(1)])
    db.save_coverage(run_id, {"ports": {"status": "ok"}})
    assert "ports.not_evaluated" not in {f["rule"] for f in rules.evaluate(run_id)["findings"]}


def test_verdict_carries_coverage(db):
    run_id = _run(db, [_proc(1)])
    db.save_coverage(run_id, {"ports": {"status": "denied"}})
    assert rules.evaluate(run_id)["coverage"] == {"ports": {"status": "denied"}}


# ----------------------------------------------------------------- report

def test_report_shows_ports_not_collected_instead_of_vanishing(db):
    """The original defect in one assertion: a denied ports scan must render an
    explicit 'not collected' line, never an absent section that reads as
    'nothing listening'."""
    run_id = _run(db, [_proc(1)])
    db.save_coverage(run_id, {"ports": {"status": "denied"}})
    rules.evaluate_and_save(run_id)
    md = report.markdown_report(run_id)
    assert "## Listening ports" in md
    assert "Not collected" in md
    assert "partial coverage" in md
    assert "## Coverage" in md


def test_report_footnotes_partial_memory(db):
    run_id = _run(db, [_proc(1), _proc(2, rss_mb=None, cpu=None)])
    db.save_coverage(run_id, coverage.compute(run_id, ports_denied=False))
    rules.evaluate_and_save(run_id)
    md = report.markdown_report(run_id)
    assert "undercount" in md
    assert "## Coverage" in md


def test_full_coverage_report_has_no_coverage_noise(db):
    """A machine where everything was readable must read exactly as before —
    no coverage section, no qualifier. The gap-disclosure is for gaps only."""
    run_id = _run(db, [_proc(1), _proc(2)])
    db.save_coverage(run_id, coverage.compute(run_id, ports_denied=False))
    rules.evaluate_and_save(run_id)
    md = report.markdown_report(run_id)
    assert "## Coverage" not in md
    assert "partial coverage" not in md
    assert "Not collected" not in md
