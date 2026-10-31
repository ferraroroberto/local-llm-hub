"""Resource watch: thresholds, debounce, unknown, stall episodes and delivery.

The probe is driven with synthetic counter windows and a fake clock so the
conditions are exact; only the delivery test runs a real subprocess (a stub
script standing in for the fleet's Telegram transport).
"""

from __future__ import annotations

import copy
import sys
import time
from collections import namedtuple
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import server as server_mod
from src.health import disk_counters, resource_alert, resource_probe, resource_rules as rules, settings
from src.health import resource_watch
from src.health.resource_alert import send_via_notify_script as real_send  # bound before conftest blocks the module name
from src.health.resource_store import ResourceStore

T0 = 1_800_000_000.0
STEP = 60.0


def _cfg(**over):
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg["resource"].update(over)
    return cfg


def _drive(free_gb=500.0, read_ms=2.0, read_ios=100, lat="ok", free="ok", disk="PhysicalDrive1"):
    row = {"disk": disk, "free": free, "lat": lat}
    if free == "ok":
        row.update(free_gb=free_gb, total_gb=1000.0)
    if lat == "ok":
        row.update(src=rules.LATENCY_SOURCE, read_ms=read_ms, write_ms=1.0, read_ios=read_ios, write_ios=100)
    return row


def _sample(i, *, ram=40.0, drives=None, drives_state="ok"):
    ram_row = {"state": "ok", "free_percent": ram, "free_gb": round(ram * 0.64, 1)} if ram is not None else {"state": "unknown"}
    return {"ts": T0 + i * STEP, "ram": ram_row, "drives_state": drives_state,
            "drives": {"E:": _drive()} if drives is None else drives, "sample_ms": 0.1}


def _history(n, **kw):
    return [_sample(i, **kw) for i in range(n)]


def _states(history, cfg=None):
    conds = rules.evaluate(history, (cfg or settings.DEFAULTS)["resource"])
    return {c["key"]: c["state"] for c in conds}


# ------------------------------------------------------------------ thresholds

def test_ram_alert_needs_a_sustained_breach_not_one_sample():
    one_dip = _history(5) + [_sample(5, ram=4.0)]
    assert _states(one_dip)["ram"] == "ok"
    sustained = _history(2) + [_sample(i, ram=4.0) for i in range(2, 5)]
    assert _states(sustained)["ram"] == "alert"


def test_ram_exactly_at_the_limit_is_not_a_breach():
    assert _states(_history(5, ram=10.0))["ram"] == "ok"


def test_a_drive_under_the_free_space_limit_alerts_and_names_the_drive():
    low = {"E:": _drive(free_gb=6.0), "C:": _drive(free_gb=300.0, disk="PhysicalDrive2")}
    conds = rules.evaluate(_history(4, drives=low), settings.DEFAULTS["resource"])
    by_key = {c["key"]: c for c in conds}
    assert by_key["disk_free:E:"]["state"] == "alert"
    assert "E:" in by_key["disk_free:E:"]["reason"]
    assert by_key["disk_free:C:"]["state"] == "ok"


def test_latency_alert_after_two_slow_minutes_naming_the_drive():
    slow = {"E:": _drive(read_ms=1700.0)}
    assert _states(_history(1) + _history(1, drives=slow))["disk_latency:E:"] == "ok"
    hist = [_sample(0), _sample(1, drives=slow), _sample(2, drives=slow)]
    conds = {c["key"]: c for c in rules.evaluate(hist, settings.DEFAULTS["resource"])}
    assert conds["disk_latency:E:"]["state"] == "alert"
    assert "E:" in conds["disk_latency:E:"]["reason"] and "1700" in conds["disk_latency:E:"]["reason"]


def test_a_near_idle_disk_with_one_slow_read_is_not_a_stall():
    quiet_but_slow = {"E:": _drive(read_ms=2000.0, read_ios=1)}
    assert _states(_history(6, drives=quiet_but_slow))["disk_latency:E:"] == "ok"


# --------------------------------------------------------------------- unknown

def test_an_unreadable_ram_probe_is_unknown_never_ok():
    assert _states(_history(5, ram=None))["ram"] == "unknown"


def test_an_unreadable_latency_or_free_space_is_unknown_never_ok():
    broken = {"E:": _drive(lat="unknown", free="unknown")}
    states = _states(_history(5, drives=broken))
    assert states["disk_latency:E:"] == "unknown"
    assert states["disk_free:E:"] == "unknown"
    assert rules.overall(rules.evaluate(_history(5, drives=broken), settings.DEFAULTS["resource"])) == "unknown"


def test_an_unreadable_drive_list_is_unknown():
    hist = _history(5, drives={}, drives_state="unknown")
    assert _states(hist)["drives"] == "unknown"


def test_alert_outranks_unknown_and_unsupported_does_not_hold_the_result_at_unknown():
    mixed = {"E:": _drive(free_gb=2.0), "/": _drive(lat="unsupported")}
    conds = rules.evaluate(_history(4, drives=mixed), settings.DEFAULTS["resource"])
    assert rules.overall(conds) == "alert"
    only_unsupported = rules.evaluate(_history(4, drives={"/": _drive(lat="unsupported")}), settings.DEFAULTS["resource"])
    assert rules.overall(only_unsupported) == "ok"
    assert rules.overall([]) == "unknown"


# --------------------------------------------------------------------- episodes

def _series(ms_by_minute, gap_after=None):
    out = []
    for i, ms in enumerate(ms_by_minute):
        row = _drive(read_ms=ms) if ms is not None else _drive(lat="unknown")
        offset = i + (5 if gap_after is not None and i > gap_after else 0)
        out.append({"ts": T0 + offset * STEP, "row": row})
    return out


def test_episodes_count_sustained_runs_with_minutes_and_worst_latency():
    series = _series([2, 800, 1700, 900, 2, 2, 600, 2, 650, 700, 2])
    eps = rules.stall_episodes(series, settings.DEFAULTS["resource"], STEP)
    assert [(e["minutes"], e["worst_ms"]) for e in eps] == [(3.0, 1700.0), (2.0, 700.0)]


def test_an_unread_minute_ends_an_episode_instead_of_bridging_it():
    eps = rules.stall_episodes(_series([800, 900, None, 900, 800]), settings.DEFAULTS["resource"], STEP)
    assert [e["minutes"] for e in eps] == [2.0, 2.0]


def test_a_hub_outage_gap_does_not_bridge_two_stalls():
    eps = rules.stall_episodes(_series([800, 900, 900, 800], gap_after=1), settings.DEFAULTS["resource"], STEP)
    assert [e["minutes"] for e in eps] == [2.0, 2.0]


def test_one_slow_minute_is_not_an_episode():
    assert rules.stall_episodes(_series([2, 900, 2, 900, 2]), settings.DEFAULTS["resource"], STEP) == []


def test_summary_counts_episodes_and_reports_coverage():
    series = _series([2] * 20 + [800, 1700, 900] + [2] * 20)
    now = series[-1]["ts"]
    out = rules.drive_summary("E:", series, settings.DEFAULTS["resource"], now=now, interval_s=STEP, window_days=7)
    assert (out["episodes"], out["total_minutes"], out["worst_ms"]) == (1, 3.0, 1700.0)
    assert out["covered_percent"] == 100.0
    assert "1 stall" in out["line"] and "1700" in out["line"]


def test_a_quiet_but_barely_measured_week_is_unknown_not_clean():
    series = _series([2, None, None, None, None, None, None, None, None, 2])
    now = series[-1]["ts"]
    out = rules.drive_summary("E:", series, settings.DEFAULTS["resource"], now=now, interval_s=STEP, window_days=7)
    assert out["state"] == "unknown" and out["episodes"] == 0
    assert "not confirmed" in out["line"]


def test_hub_downtime_inside_the_window_lowers_coverage():
    series = _series([2] * 10 + [2] * 10, gap_after=9)  # a 5 minute hole in the middle
    out = rules.drive_summary("E:", series, settings.DEFAULTS["resource"], now=series[-1]["ts"], interval_s=STEP, window_days=7)
    assert out["covered_percent"] < 100.0


# ------------------------------------------------------------------------ probe

class _FakeCounters:
    """Stands in for the PDH query: hands back one prepared window per collect."""

    def __init__(self, windows):
        self._windows = iter(windows)

    def collect(self):
        window = next(self._windows)
        if isinstance(window, Exception):
            raise window
        return window


def _win(read_s=0.0, reads=0, write_s=0.0, writes=0, disk=1):
    return {disk: {"read_s": read_s, "reads": reads, "write_s": write_s, "writes": writes}}


def _probe_with(monkeypatch, windows, *, disk_number=lambda label: 1, counters=None):
    monkeypatch.setattr(resource_probe, "fixed_mounts", lambda: ["E:\\"])
    monkeypatch.setattr(resource_probe, "drive_label", lambda mount: mount[:2])
    monkeypatch.setattr(resource_probe.psutil, "disk_usage",
                        lambda mount: namedtuple("U", "total used free percent")(10 * 1024 ** 4, 0, 9 * 1024 ** 4, 10.0))
    fake = _FakeCounters(windows)
    return resource_probe.ResourceProbe(disk_number=disk_number, counters=counters or (lambda: fake))


def test_latency_is_the_per_operation_average_over_the_window(monkeypatch):
    probe = _probe_with(monkeypatch, [None, _win(read_s=1.7, reads=20)])
    probe.prime()
    row = probe.sample()["drives"]["E:"]
    assert row["lat"] == "ok" and row["src"] == rules.LATENCY_SOURCE
    assert row["read_ms"] == 1700.0 and row["read_ios"] == 20 and row["disk"] == "PhysicalDrive1"
    assert row["write_ms"] is None and row["write_ios"] == 0


def test_a_fast_disk_keeps_its_sub_millisecond_latency():
    assert resource_probe.direction(0.000024, 1197.4, "write") == {"ios": 1197, "ms": 0.024}


def test_many_operations_with_zero_accumulated_time_is_unknown_not_zero_ms(monkeypatch):
    probe = _probe_with(monkeypatch, [None, _win(read_s=0.0, reads=1672, write_s=0.0, writes=752)] * 3)
    probe.prime()
    samples = [probe.sample() for _ in range(3)]
    row = samples[-1]["drives"]["E:"]
    assert row["lat"] == "unknown" and "not credible" in row["why"]
    cond = {c["key"]: c for c in rules.evaluate(samples, settings.DEFAULTS["resource"])}["disk_latency:E:"]
    assert cond["state"] == "unknown" and "not credible" in cond["reason"]


def test_a_quiet_disk_with_a_few_instant_operations_is_still_a_reading():
    assert resource_probe.direction(0.0, 3, "read") == {"ios": 3, "ms": 0.0}
    assert resource_probe.direction(0.0, 0, "read") == {"ios": 0, "ms": None}


def test_the_first_sample_without_a_window_is_unknown(monkeypatch):
    probe = _probe_with(monkeypatch, [None])
    assert probe.sample()["drives"]["E:"]["lat"] == "unknown"


def test_a_drive_that_cannot_be_mapped_to_a_disk_is_unknown(monkeypatch):
    def boom(label):
        raise OSError("no handle")

    probe = _probe_with(monkeypatch, [None, _win(read_s=0.01, reads=10)], disk_number=boom)
    probe.prime()
    row = probe.sample()["drives"]["E:"]
    assert row["lat"] == "unknown" and row["free"] == "ok"


def test_a_disk_missing_from_the_window_is_unknown(monkeypatch):
    probe = _probe_with(monkeypatch, [None, _win(read_s=0.01, reads=10, disk=2)])
    probe.prime()
    assert probe.sample()["drives"]["E:"]["lat"] == "unknown"


def test_failed_counters_unavailable_counters_and_no_mapping_support_are_distinct(monkeypatch):
    unsupported = _probe_with(monkeypatch, [None])
    unsupported._disk_number = None
    assert unsupported.sample()["drives"]["E:"]["lat"] == "unsupported"
    failed = _probe_with(monkeypatch, [None, OSError("PDH status 0xC0000BC6")])
    failed.prime()
    assert failed.sample()["drives"]["E:"]["lat"] == "unknown"
    unavailable = _probe_with(monkeypatch, [], counters=lambda: None)
    assert unavailable.sample()["drives"]["E:"]["lat"] == "unknown"


def test_stored_rows_from_an_untrusted_source_never_read_as_no_stalls():
    old = [{"ts": T0 + i * STEP, "row": {**_drive(read_ms=0.0), "src": None}} for i in range(60)]
    now = old[-1]["ts"]
    out = rules.drive_summary("E:", old, settings.DEFAULTS["resource"], now=now, interval_s=STEP, window_days=7)
    assert out["state"] == "unknown" and out["covered_percent"] == 0.0 and "not confirmed" in out["line"]
    stale_hist = [{**_sample(i), "drives": {"E:": e["row"]}} for i, e in enumerate(old[-3:])]
    assert _states(stale_hist)["disk_latency:E:"] == "unknown"


@pytest.mark.skipif(sys.platform != "win32", reason="PDH counters are Windows-only")
def test_the_real_performance_counters_return_a_window_per_physical_disk():
    counters = disk_counters.DiskCounters()
    try:
        assert counters.collect() is None
        time.sleep(0.2)
        window = counters.collect()
    finally:
        counters.close()
    assert window and all(isinstance(n, int) for n in window)
    for values in window.values():
        assert set(values) == {"read_s", "write_s", "reads", "writes"}
        assert all(v >= 0 for v in values.values())


# ------------------------------------------------------------- store + debounce

def _store(tmp_path):
    return ResourceStore(tmp_path / "r.sqlite3")


def test_history_is_kept_for_the_retention_window_only(tmp_path):
    store = _store(tmp_path)
    for day in range(20):
        store.add_sample({"ts": T0 + day * 86400.0, "ram": {}, "drives": {}}, keep_days=14)
    kept = store.samples_since(0)
    assert len(kept) == 15 and kept[0]["ts"] == T0 + 5 * 86400.0


def _firing(key="ram"):
    return [{"key": key, "kind": "ram", "drive": None, "state": "alert", "reason": "free RAM 4% (2 GB), limit 10%"}]


def test_a_second_alert_inside_six_hours_stays_quiet_then_resumes(tmp_path):
    sent = []
    gate = resource_alert.AlertGate(_store(tmp_path), lambda text, cfg: (sent.append(text) or True, None))
    cfg = settings.DEFAULTS["resource"]["alert"]
    assert len(gate.process(_firing(), cfg, now=T0, host_label="tower")) == 1
    assert gate.process(_firing(), cfg, now=T0 + 5 * 3600, host_label="tower") == []
    assert len(sent) == 1 and sent[0].startswith("tower: free RAM")
    assert len(gate.process(_firing(), cfg, now=T0 + 6 * 3600 + 1, host_label="tower")) == 1


def test_the_debounce_survives_a_restart_and_is_per_condition(tmp_path):
    sent = []
    send = lambda text, cfg: (sent.append(text) or True, None)  # noqa: E731
    cfg = settings.DEFAULTS["resource"]["alert"]
    resource_alert.AlertGate(_store(tmp_path), send).process(_firing(), cfg, now=T0, host_label="h")
    reborn = resource_alert.AlertGate(_store(tmp_path), send)
    assert reborn.process(_firing(), cfg, now=T0 + 60, host_label="h") == []
    assert len(reborn.process(_firing("disk_free:E:"), cfg, now=T0 + 60, host_label="h")) == 1


def test_a_failed_delivery_is_recorded_as_failed_and_retried_later(tmp_path):
    results = iter([(False, "boom"), (True, None)])
    gate = resource_alert.AlertGate(_store(tmp_path), lambda text, cfg: next(results))
    cfg = settings.DEFAULTS["resource"]["alert"]
    first = gate.process(_firing(), cfg, now=T0, host_label="h")
    assert first[0]["delivery"] == "failed" and first[0]["error"] == "boom"
    assert gate.process(_firing(), cfg, now=T0 + 60, host_label="h") == []           # inside retry_after_s
    retry = gate.process(_firing(), cfg, now=T0 + cfg["retry_after_s"] + 1, host_label="h")
    assert retry[0]["delivery"] == "sent"
    assert gate.process(_firing(), cfg, now=T0 + cfg["retry_after_s"] + 120, host_label="h") == []  # cooldown now


def test_alerts_can_be_switched_off():
    cfg = dict(settings.DEFAULTS["resource"]["alert"], enabled=False)
    gate = resource_alert.AlertGate(ResourceStore(Path("unused.sqlite3")), lambda t, c: pytest.fail("sent"))
    assert gate.process(_firing(), cfg, now=T0, host_label="h") == []


# ---------------------------------------------------------------------- the loop

class _FakeProbe:
    def __init__(self):
        self.i = 0
        self.ram = 40.0
        self.lat = "ok"

    def prime(self):
        pass

    def sample(self):
        self.i += 1
        s = _sample(self.i, ram=self.ram, drives={"E:": _drive(lat=self.lat)})
        s["ts"] = T0 + self.i * STEP
        return s


@pytest.fixture()
def watch(tmp_path):
    sent = []
    w = resource_watch.ResourceWatch(_FakeProbe(), _store(tmp_path), lambda text, cfg: (sent.append(text) or True, None))
    w.sent = sent
    return w


def test_a_forced_threshold_sends_one_line_and_a_second_inside_six_hours_is_quiet(watch):
    cfg = _cfg()
    cfg["resource"]["ram_free_percent"]["alert_below"] = 101       # force: 40% free is now "low"
    for _ in range(3):
        out = watch.tick(cfg)
    assert out["state"] == "alert" and len(watch.sent) == 1
    for _ in range(30):
        watch.tick(cfg)
    assert len(watch.sent) == 1                                     # still firing, still quiet
    assert [e["delivery"] for e in watch.current(now=T0 + 40 * STEP)["alerts"]] == ["sent"]


def test_a_failed_read_reports_unknown_and_sends_nothing(watch):
    watch._probe.lat = "unknown"
    watch._probe.ram = None
    for _ in range(5):
        out = watch.tick(_cfg())
    assert out["state"] == "unknown" and watch.sent == []
    assert "could not be read" in out["reason"]


def test_before_any_sample_and_when_stale_the_state_is_unknown(watch):
    assert watch.current()["state"] == "unknown"
    out = watch.tick(_cfg())
    assert out["state"] == "ok"
    stale_at = out["sampled_at"] + 4 * STEP
    assert watch.current(now=stale_at)["state"] == "unknown"


def test_the_weekly_summary_counts_a_stall_across_the_stored_history(watch):
    for minute in range(30):
        watch._probe.lat = "ok"
        watch.tick(_cfg())
    stalling = watch._probe
    original = stalling.sample

    def slow_sample():
        s = original()
        s["drives"]["E:"] = _drive(read_ms=1700.0)
        return s

    stalling.sample = slow_sample
    for _ in range(4):
        out = watch.tick(_cfg())
    drive = next(d for d in out["stalls"]["drives"] if d["drive"] == "E:")
    assert drive["episodes"] == 1 and drive["worst_ms"] == 1700.0
    assert "1 stall" in out["stalls"]["line"]


def test_an_unwritable_history_store_is_reported_not_hidden(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    w = resource_watch.ResourceWatch(_FakeProbe(), ResourceStore(blocker / "r.sqlite3"), lambda t, c: (True, None))
    out = w.tick(_cfg())
    assert out["store"] == "failed" and out["stalls"]["state"] == "unknown"


def test_the_endpoint_and_the_machine_card_carry_the_summary(watch, monkeypatch):
    monkeypatch.setattr(resource_watch, "WATCH", watch)
    watch.tick(_cfg())
    client = TestClient(server_mod.app)
    body = client.get("/admin/api/machines/resources").json()
    assert body["state"] == "ok" and body["host_id"]
    assert body["stalls"]["drives"][0]["drive"] == "E:"
    host = next(m for m in client.get("/admin/api/machines/status").json()["machines"] if m["is_host"])
    assert host["resources"]["state"] == "ok" and host["resources"]["stalls"]["drives"]


# --------------------------------------------------------------------- delivery

_STUB = """
import pathlib, sys
pathlib.Path(sys.argv[0]).with_suffix(".out").write_text(" ".join(sys.argv[1:]) + "|" + sys.stdin.buffer.read().decode("utf-8"), encoding="utf-8")
sys.exit(int(pathlib.Path(sys.argv[0]).with_suffix(".code").read_text()) if pathlib.Path(sys.argv[0]).with_suffix(".code").exists() else 0)
"""


def test_delivery_runs_the_fleet_transport_with_the_message_on_stdin(tmp_path):
    script = tmp_path / "notify_stub.py"
    script.write_text(_STUB, encoding="utf-8")
    cfg = dict(settings.DEFAULTS["resource"]["alert"], script=str(script))
    delivered, error = real_send("tower: E: stalled \u2014 1700 ms", cfg)
    assert (delivered, error) == (True, None)
    assert script.with_suffix(".out").read_text(encoding="utf-8") == "--category attention|tower: E: stalled \u2014 1700 ms"


def test_delivery_failures_are_reported_with_a_reason(tmp_path):
    missing = real_send("x", dict(settings.DEFAULTS["resource"]["alert"], script=str(tmp_path / "nope.py")))
    assert missing[0] is False and "not found" in missing[1]
    script = tmp_path / "notify_stub.py"
    script.write_text(_STUB, encoding="utf-8")
    script.with_suffix(".code").write_text("3")
    failed = real_send("x", dict(settings.DEFAULTS["resource"]["alert"], script=str(script)))
    assert failed[0] is False and "exited 3" in failed[1]


def test_the_committed_config_documents_the_resource_defaults():
    assert settings.load()["resource"] == settings.DEFAULTS["resource"]


def test_a_young_history_says_how_long_it_has_watched():
    series = _series([2] * 30)
    cfg = settings.DEFAULTS["resource"]
    minutes = rules.drive_summary("E:", series, cfg, now=series[-1]["ts"], interval_s=STEP, window_days=7)
    assert "min watched so far" in minutes["line"]
    hours = _series([2] * 180)
    out = rules.drive_summary("E:", hours, cfg, now=hours[-1]["ts"], interval_s=STEP, window_days=7)
    assert "h watched so far" in out["line"]
