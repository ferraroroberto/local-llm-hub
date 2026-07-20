"""Diagnostics health-verdict engine (#315).

Every rule is a pure function of stored rows, so these tests build synthetic
captures and assert on the verdict — no live system involved, which is what
makes the thresholds safe to retune.
"""

from __future__ import annotations

import json
import time

import pytest

from src.diagnostics import rules, store


@pytest.fixture()
def db(tmp_path):
    store.set_db_path(tmp_path / "diag.db")
    rules.set_rules_path(None)
    rules.reload_thresholds()
    yield store
    store.set_db_path(None)
    rules.set_rules_path(None)
    rules.reload_thresholds()


def _sample(ts, *, cpu=5.0, ram=30.0, swap=2.0, disk=20.0, procs=100, gpus=None):
    return store.SystemSample(
        ts=ts, cpu_percent=cpu, per_core=[cpu], load_avg=None,
        ram={"used_gb": 4.0, "total_gb": 16.0, "percent": ram},
        swap={"used_gb": 0.2, "total_gb": 8.0, "percent": swap},
        disk={"used_gb": 100.0, "total_gb": 500.0, "percent": disk},
        disk_io={}, net_io={}, gpus=gpus or [], process_count=procs,
    )


def _proc(pid, name, app_id, rss_mb=10, cpu=1.0, status="running"):
    return {"pid": pid, "ppid": 1, "name": name, "cmdline": f"/bin/{name} {pid}",
            "app_id": app_id, "cpu_percent": cpu, "rss_bytes": int(rss_mb * 1024 ** 2),
            "num_threads": 2, "status": status, "create_time": 1.0}


def _run_with(db, samples, processes=None, ports=None):
    run_id = db.create_run(machine_id="pc-test", os_name="TestOS",
                           interval_s=15.0, duration_s=60.0)
    for s in samples:
        db.write_sample(run_id, s, processes or [], ports or [])
    db.finish_run(run_id)
    return run_id


def _rules_of(result):
    return {f["rule"] for f in result["findings"]}


def test_quiet_machine_is_healthy(db):
    run_id = _run_with(db, [_sample(time.time() + i) for i in range(5)],
                       [_proc(1, "python.exe", "local-llm-hub")])
    result = rules.evaluate(run_id)
    assert result["level"] == "healthy"
    assert result["findings"] == []


def test_empty_run_is_healthy_not_an_error(db):
    run_id = db.create_run(machine_id="pc-test", os_name="TestOS",
                           interval_s=15.0, duration_s=60.0)
    result = rules.evaluate(run_id)
    assert result == {"level": "healthy", "findings": [], "sample_count": 0}


def test_sustained_cpu_fires_but_a_single_spike_does_not(db):
    """The rule is about *sustained* load — one 100% tick during a model load
    is normal and must not raise a finding."""
    spiky = [_sample(time.time() + i, cpu=5.0) for i in range(9)]
    spiky.append(_sample(time.time() + 9, cpu=99.0))
    assert "cpu.sustained" not in _rules_of(rules.evaluate(_run_with(db, spiky)))

    pinned = [_sample(time.time() + i, cpu=97.0) for i in range(10)]
    result = rules.evaluate(_run_with(db, pinned))
    assert "cpu.sustained" in _rules_of(result)
    assert result["level"] == "critical"


def test_ram_and_swap_pressure_escalate(db):
    run_id = _run_with(db, [_sample(time.time() + i, ram=97.0, swap=80.0) for i in range(3)])
    result = rules.evaluate(run_id)
    assert {"ram.pressure", "swap.pressure"} <= _rules_of(result)
    assert result["level"] == "critical"


def test_disk_capacity_warning(db):
    run_id = _run_with(db, [_sample(time.time() + i, disk=88.0) for i in range(3)])
    result = rules.evaluate(run_id)
    assert "disk.capacity" in _rules_of(result)
    assert result["level"] == "warning"


def test_gpu_vram_saturation_is_reported_per_gpu(db):
    gpus = [{"name": "RTX 4080", "vram_percent": 98.0},
            {"name": "RTX 3060", "vram_percent": 10.0}]
    run_id = _run_with(db, [_sample(time.time(), gpus=gpus)])
    findings = [f for f in rules.evaluate(run_id)["findings"] if f["rule"] == "gpu.vram"]
    assert len(findings) == 1
    assert findings[0]["evidence"]["gpu"] == "RTX 4080"


def test_per_app_process_ceiling_fires_for_a_real_app(db):
    procs = [_proc(i, "python.exe", "app-launcher") for i in range(1, 45)]
    run_id = _run_with(db, [_sample(time.time())], procs)
    result = rules.evaluate(run_id)
    finding = next(f for f in result["findings"] if f["rule"] == "processes.per_app")
    assert finding["evidence"]["app_id"] == "app-launcher"
    assert result["level"] == "critical"


def test_browser_engines_are_not_judged_as_one_app(db):
    """A browser/webview is process-per-tab by design — 40 WebKit helpers is
    normal, and reporting it critical is exactly the cry-wolf failure the
    ignore list exists to prevent."""
    procs = [_proc(i, "WebKitNetworkProcess", "webkit") for i in range(1, 45)]
    result = rules.evaluate(_run_with(db, [_sample(time.time())], procs))
    assert [f for f in result["findings"] if f["rule"] == "processes.per_app"] == []


def test_aggregate_buckets_are_not_judged_as_one_app(db):
    """`unattributed` and the OS buckets are collections of unrelated
    processes, not an app — counting them as one made a healthy box report
    critical on every single run."""
    procs = ([_proc(i, "svchost.exe", "windows-services") for i in range(1, 120)]
             + [_proc(500 + i, f"misc{i}.exe", "unattributed") for i in range(1, 200)])
    result = rules.evaluate(_run_with(db, [_sample(time.time(), procs=320)], procs))
    per_app = [f for f in result["findings"] if f["rule"] == "processes.per_app"]
    assert per_app == []


def test_heavyweight_unattributed_processes_are_surfaced(db):
    """The bloat review list: big things nobody has accounted for."""
    procs = [_proc(1, "MysteryApp.exe", "unattributed", rss_mb=900),
             _proc(2, "small.exe", "unattributed", rss_mb=5)]
    result = rules.evaluate(_run_with(db, [_sample(time.time())], procs))
    finding = next(f for f in result["findings"] if f["rule"] == "processes.unattributed")
    assert finding["evidence"]["count"] == 1
    assert finding["evidence"]["top"][0]["name"] == "MysteryApp.exe"


def test_duplicate_port_listener_is_flagged(db):
    ports = [{"port": 8000, "proto": "tcp", "pid": 1, "name": "a", "app_id": "app-a"},
             {"port": 8000, "proto": "tcp", "pid": 2, "name": "b", "app_id": "app-b"}]
    result = rules.evaluate(_run_with(db, [_sample(time.time())], [], ports))
    assert "ports.duplicate" in _rules_of(result)


def test_single_owner_port_is_not_flagged(db):
    ports = [{"port": 8000, "proto": "tcp", "pid": 1, "name": "a", "app_id": "app-a"}]
    result = rules.evaluate(_run_with(db, [_sample(time.time())], [], ports))
    assert "ports.duplicate" not in _rules_of(result)


def test_thresholds_are_data_not_code(db, tmp_path):
    """Retuning the config file must change the verdict with no code change —
    the whole point of keeping thresholds in JSON."""
    run_id = _run_with(db, [_sample(time.time() + i, ram=60.0) for i in range(3)])
    assert rules.evaluate(run_id)["level"] == "healthy"

    custom = tmp_path / "rules.json"
    custom.write_text(json.dumps({"ram": {"percent_warn": 50, "percent_critical": 55}}),
                      encoding="utf-8")
    rules.set_rules_path(custom)
    assert rules.evaluate(run_id)["level"] == "critical"


def test_partial_config_only_overrides_what_it_names(db, tmp_path):
    custom = tmp_path / "rules.json"
    custom.write_text(json.dumps({"ram": {"percent_warn": 50}}), encoding="utf-8")
    rules.set_rules_path(custom)
    th = rules.load_thresholds()
    assert th["ram"]["percent_warn"] == 50
    assert th["disk"]["percent_warn"] == 85          # untouched default survives
    assert "per_app_ignore" in th["processes"]


def test_evaluate_and_save_persists_the_verdict(db):
    run_id = _run_with(db, [_sample(time.time() + i, ram=97.0) for i in range(3)])
    rules.evaluate_and_save(run_id)
    stored = db.get_run(run_id)
    assert stored["verdict_level"] == "critical"
    assert any(f["rule"] == "ram.pressure" for f in stored["findings"])


def test_findings_are_ordered_worst_first(db):
    run_id = _run_with(db, [_sample(time.time() + i, ram=97.0, disk=88.0) for i in range(3)])
    levels = [f["level"] for f in rules.evaluate(run_id)["findings"]]
    assert levels == sorted(levels, key=lambda l: -{"critical": 2, "warning": 1}.get(l, 0))
