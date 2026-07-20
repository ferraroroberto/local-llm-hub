"""Portable capture + foreign-run ingest (#316).

The contract these pin: a capture taken on a hub-less machine and replayed
through :mod:`src.diagnostics.ingest` becomes a run that ``report``/``rules``
handle identically to a locally captured one — with attribution, coverage, and
the verdict all applied *centrally at ingest* against the **source** machine's
platform, never the ingesting hub's. And the portable script stays standalone:
it must import nothing from ``src/`` (it runs where ``src/`` does not exist).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from src.diagnostics import attribution, coverage, ingest, report, rules, store

PORTABLE = Path(__file__).resolve().parent.parent / "scripts" / "portable_capture.py"


@pytest.fixture()
def db(tmp_path):
    store.set_db_path(tmp_path / "diag.db")
    rules.set_rules_path(None)
    rules.reload_thresholds()
    # Real config/diagnostics_apps.json, clean per-platform attribution cache.
    attribution.set_rules_path(None)
    attribution.set_platform(None)
    yield store
    store.set_db_path(None)
    attribution.set_rules_path(None)
    attribution.set_platform(None)


# ------------------------------------------------------------- builders


def _proc(pid, name, cmdline, *, cpu=1.0, rss_mb=10, num_threads=2, status="running"):
    return {"pid": pid, "ppid": 1, "name": name, "cmdline": cmdline,
            "cpu_percent": cpu,
            "rss_bytes": int(rss_mb * 1024 ** 2) if rss_mb is not None else None,
            "num_threads": num_threads, "status": status, "create_time": 1.0}


def _sample(procs, ports=(), *, ts=1000.0, ports_denied=False, cpu=10.0):
    return {"ts": ts, "cpu_percent": cpu, "per_core": [cpu], "load_avg": None,
            "ram": {"used_gb": 4.0, "total_gb": 16.0, "percent": 30.0},
            "swap": {"used_gb": 0.0, "total_gb": 8.0, "percent": 0.0},
            "disk": {"used_gb": 10.0, "total_gb": 500.0, "percent": 20.0},
            "disk_io": {}, "net_io": {}, "gpus": [], "process_count": len(procs),
            "ports_denied": ports_denied, "processes": list(procs), "ports": list(ports)}


def _payload(samples, *, platform="linux", machine="peer", cpu_count=8):
    return {"schema": "llm-hub-diagnostics-capture/1", "machine": machine,
            "hostname": machine, "os": f"{platform}-os", "platform": platform,
            "cpu_count": cpu_count, "interval_s": 15.0,
            "started_at": 1000.0, "ended_at": 1060.0, "samples": samples}


# ------------------------------------------------- the standalone constraint


def test_portable_capture_imports_nothing_from_src():
    """It runs on a machine with no checkout, so a single ``from src...`` import
    would break the whole delivery. Asserted structurally against the source."""
    tree = ast.parse(PORTABLE.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [n.name for n in node.names if n.name.split(".")[0] == "src"]
        elif isinstance(node, ast.ImportFrom):
            # A relative import (level>0) or any `from src...` is disqualifying.
            if node.level and node.level > 0:
                offenders.append(f"relative import (level {node.level})")
            elif (node.module or "").split(".")[0] == "src":
                offenders.append(node.module)
    assert offenders == [], f"portable_capture imports project code: {offenders}"


def test_portable_and_ingest_agree_on_schema_and_platform_vocab(monkeypatch):
    """The two ends of the wire must share the envelope, or a real capture would
    be refused by its own ingest."""
    spec = importlib.util.spec_from_file_location("portable_capture", PORTABLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.SCHEMA.startswith(ingest.SCHEMA_PREFIX)
    # The token the script emits is always one the ingest accepts. `mod.sys` is
    # the real `sys` singleton, so patch through monkeypatch — a bare assignment
    # would leave `sys.platform` corrupted for every later test in the process.
    for raw in ("win32", "darwin", "linux", "linux2"):
        monkeypatch.setattr(mod.sys, "platform", raw)
        assert mod._platform_token() in ingest._VALID_PLATFORMS


# --------------------------------------------------------- the happy path


def test_ingest_produces_a_run_readers_handle_like_local(db):
    procs = [_proc(1, "python3", "/opt/automation/grocery/.venv/bin/python3 -m app", cpu=5.0),
             _proc(2, "Mystery", "/home/x/Mystery", rss_mb=900)]
    run_id = ingest.ingest_payload(_payload([_sample(procs)]))

    run = store.get_run(run_id)
    assert run["trigger"] == "remote"
    assert run["machine_id"] == "peer"
    assert run["params"]["source_platform"] == "linux"

    s = report.summary(run_id)
    assert s is not None
    app_ids = {a["app_id"] for a in s["apps"]}
    assert "grocery" in app_ids                 # attributed centrally at ingest
    assert s["verdict"]["level"] in ("healthy", "warning", "critical")
    # report renders without raising, exactly like a local run.
    assert report.markdown_report(run_id).startswith("# Machine diagnostics")


def test_ingest_normalizes_cpu_against_the_source_machine_cores(db):
    """A summary read on the Windows hub must divide per-process CPU by the
    *peer's* core count, taken from the payload, not the hub's."""
    run_id = ingest.ingest_payload(_payload([_sample([_proc(1, "p", "/opt/automation/grocery/x")])],
                                            cpu_count=32))
    assert report.summary(run_id)["cpu_count"] == 32
    assert store.get_run(run_id)["params"]["cpu_count"] == 32


def test_attribution_uses_the_source_platforms_rule_group(db):
    """The crux of tying #320 (per-OS rules) to #316 (cross-OS replay): a Linux
    capture must be judged by the Linux rules even though the ingest runs on
    Windows — `/usr/lib/systemd/...` is Linux-system, and `/usr/bin` is *not*
    bucketed on Linux (it is user software there), which is the exact opposite
    of macOS."""
    procs = [_proc(1, "systemd-logind", "/usr/lib/systemd/systemd-logind"),
             _proc(2, "mystery", "/usr/bin/mystery --serve")]
    run_id = ingest.ingest_payload(_payload([_sample(procs)], platform="linux"))
    app_ids = {a["app_id"] for a in store.app_aggregates(run_id)}
    assert "linux-system" in app_ids
    assert "unattributed" in app_ids            # /usr/bin stays reviewable on Linux


def test_same_path_attributes_differently_per_source_os(db):
    """`/usr/bin/foo` is Apple-owned on macOS but user software on Linux — the
    ingest honours the source OS, so the identical path lands in different
    buckets depending on where the capture came from."""
    mac = ingest.ingest_payload(_payload([_sample([_proc(1, "foo", "/usr/bin/foo")])],
                                         platform="darwin", machine="mac"))
    lin = ingest.ingest_payload(_payload([_sample([_proc(1, "foo", "/usr/bin/foo")])],
                                         platform="linux", machine="lin"))
    mac_apps = {a["app_id"] for a in store.app_aggregates(mac)}
    lin_apps = {a["app_id"] for a in store.app_aggregates(lin)}
    assert "macos-system" in mac_apps
    assert "macos-system" not in lin_apps


def test_darwin_source_marks_gpu_unsupported_even_on_a_windows_hub(db):
    """Coverage keys GPU-unsupported off the *source* platform — an Apple-silicon
    capture ingested on Windows still records the unified-memory gap (#322 tie)."""
    run_id = ingest.ingest_payload(_payload([_sample([_proc(1, "p", "/usr/bin/p")])],
                                            platform="darwin"))
    assert store.get_run(run_id)["coverage"]["gpu"]["status"] == coverage.UNSUPPORTED


def test_machine_override_beats_the_payload(db):
    """The orchestrator knows the fleet id even when the peer only knew its
    hostname."""
    run_id = ingest.ingest_payload(_payload([_sample([_proc(1, "p", "/x")])], machine="hostname-only"),
                                   machine="openclaw")
    assert store.get_run(run_id)["machine_id"] == "openclaw"


# ----------------------------------------------------------- coverage gaps


def test_all_ports_denied_records_a_coverage_gap_not_an_empty_result(db):
    """Every sample's port scan denied → coverage says `denied` and the verdict
    carries the non-escalating `ports.not_evaluated` note (#322), instead of the
    empty port table silently reading as 'nothing listening'."""
    run_id = ingest.ingest_payload(_payload([_sample([_proc(1, "p", "/x")], ports_denied=True),
                                             _sample([_proc(1, "p", "/x")], ports_denied=True)]))
    assert store.get_run(run_id)["coverage"]["ports"]["status"] == coverage.DENIED
    result = rules.evaluate(run_id)
    assert "ports.not_evaluated" in {f["rule"] for f in result["findings"]}


def test_one_readable_port_scan_clears_the_gap(db):
    port = {"port": 8000, "proto": "tcp", "address": "127.0.0.1", "pid": 1, "name": "p"}
    run_id = ingest.ingest_payload(_payload([
        _sample([_proc(1, "p", "/opt/automation/grocery/x")], [port], ports_denied=False),
        _sample([_proc(1, "p", "/opt/automation/grocery/x")], ports_denied=True),
    ]))
    cov = store.get_run(run_id)["coverage"]
    assert cov["ports"]["status"] == coverage.OK
    # the port was attributed at ingest, to the same app as its owning process
    ports = store.listening_ports(run_id)
    assert ports and ports[0]["app_id"] == "grocery"


def test_null_denied_reads_survive_the_round_trip_as_partial(db):
    """A process whose memory/CPU the peer couldn't read arrives as JSON null and
    must stay null in the store, so coverage reports `partial` — not a false
    zero (#322)."""
    procs = [_proc(1, "readable", "/x"),
             _proc(2, "denied", "/y", rss_mb=None, cpu=None)]
    run_id = ingest.ingest_payload(_payload([_sample(procs)]))
    assert store.get_run(run_id)["coverage"]["proc_mem"] == {
        "status": coverage.PARTIAL, "readable": 1, "total": 2}


# ------------------------------------------------------- refusing garbage


@pytest.mark.parametrize("bad, why", [
    (["not", "a", "dict"], "not an object"),
    ({"schema": "something-else", "platform": "linux", "samples": [1]}, "bad schema"),
    ({"schema": "llm-hub-diagnostics-capture/1", "platform": "solaris",
      "samples": [1]}, "bad platform"),
    ({"schema": "llm-hub-diagnostics-capture/1", "platform": "linux",
      "samples": []}, "no samples"),
    ({"schema": "llm-hub-diagnostics-capture/1", "platform": "linux",
      "samples": [{"ts": 1.0, "ports": []}]}, "sample missing processes"),
])
def test_malformed_payload_is_refused_before_any_run_row(db, bad, why):
    before = len(store.list_runs())
    with pytest.raises(ingest.IngestError):
        ingest.ingest_payload(bad)
    assert len(store.list_runs()) == before, f"{why}: a run row leaked"


def test_truncated_json_file_is_refused(db, tmp_path):
    f = tmp_path / "cap.json"
    f.write_text('{"schema": "llm-hub-diagnostics-capture/1", "samples": [', encoding="utf-8")
    with pytest.raises(ingest.IngestError):
        ingest.ingest_file(f)
    assert store.list_runs() == []


def test_foreign_ingest_does_not_corrupt_host_attribution(db):
    """Ingesting a Linux run must not poison the per-platform cache the live
    Windows host reads — the reason attribution is keyed per platform, not a
    mutated global."""
    win_before = attribution.attribute("llama-server.exe", "llama-server.exe --port 8088",
                                        platform="windows")
    ingest.ingest_payload(_payload([_sample([_proc(1, "systemd", "/usr/lib/systemd/systemd")])],
                                   platform="linux"))
    assert attribution.attribute("llama-server.exe", "llama-server.exe --port 8088",
                                 platform="windows") == win_before == "llama.cpp"
