"""Disk load: a physical disk that is busy, queued and slow together, sustained.

Every case is driven with injected counter values, never a real load. The
reference reading is the measured starvation: 100 % busy, queue 14 to 34,
about 240 ms per read, for several minutes.
"""

from __future__ import annotations

import logging

from src.health import classifier, resource_watch, settings, watch
from src.health import resource_rules as rules
from src.health.resource_store import ResourceStore

from .test_resource_watch import T0, _cfg, _drive, _sample

LOAD = settings.DEFAULTS["resource"]["disk_load"]


def _hot(**over):
    return {"E:": _drive(read_ms=240.0, busy=100.0, queue=25.0, **over)}


def _state(history, key="disk_load:PhysicalDrive1"):
    conds = {c["key"]: c for c in rules.evaluate(history, settings.DEFAULTS["resource"])}
    return conds[key]


def _run(hot_minutes, *, before=2, drives=None):
    """``before`` calm samples, then ``hot_minutes`` overloaded ones."""
    hist = [_sample(i) for i in range(before)]
    hist += [_sample(before + i, drives=drives or _hot()) for i in range(hot_minutes)]
    return hist


def test_a_short_burst_never_warns_or_alerts():
    assert _state(_run(2))["state"] == "ok"
    burst_then_calm = _run(2) + [_sample(10)]
    assert _state(burst_then_calm)["state"] == "ok"


def test_sustained_overload_warns_first_then_alerts():
    assert _state(_run(LOAD["watch_sustain_samples"] - 1))["state"] == "ok"
    assert _state(_run(LOAD["watch_sustain_samples"]))["state"] == "watch"
    assert _state(_run(LOAD["alert_sustain_samples"] - 1))["state"] == "watch"
    alert = _state(_run(LOAD["alert_sustain_samples"]))
    assert alert["state"] == "alert"
    assert "E:" in alert["reason"] and "100% busy" in alert["reason"] and "queue 25" in alert["reason"]
    assert "240 ms" in alert["reason"]


def test_one_calm_minute_inside_the_run_restarts_the_count():
    hist = _run(4) + [_sample(20)] + [_sample(21 + i, drives=_hot()) for i in range(2)]
    assert _state(hist)["state"] == "ok"


def test_a_busy_disk_with_a_short_queue_and_fast_operations_is_a_healthy_copy():
    copying = {"E:": _drive(read_ms=3.0, busy=100.0, queue=1.5)}
    assert _state(_run(20, drives=copying))["state"] == "ok"


def test_a_deep_queue_on_a_fast_disk_is_not_an_overload():
    nvme = {"E:": _drive(read_ms=1.0, busy=100.0, queue=32.0)}
    assert _state(_run(20, drives=nvme))["state"] == "ok"


def test_queued_requests_with_nothing_completing_is_the_worst_stall_not_unmeasured():
    frozen = {"E:": {**_drive(busy=100.0, queue=30.0), "read_ms": None, "write_ms": None, "read_ios": 0, "write_ios": 0}}
    assert _state(_run(LOAD["alert_sustain_samples"], drives=frozen))["state"] == "alert"


def test_drives_sharing_one_disk_are_one_condition_naming_every_letter():
    shared = {"C:": _drive(read_ms=240.0, busy=100.0, queue=25.0), "D:": _drive(read_ms=240.0, busy=100.0, queue=25.0)}
    conds = [c for c in rules.evaluate(_run(5, drives=shared), settings.DEFAULTS["resource"]) if c["kind"] == "disk_load"]
    assert [c["key"] for c in conds] == ["disk_load:PhysicalDrive1"]
    assert "C:, D:" in conds[0]["reason"]


def test_each_physical_disk_is_judged_on_its_own():
    two = {"E:": _drive(read_ms=240.0, busy=100.0, queue=25.0), "C:": _drive(disk="PhysicalDrive0")}
    states = {c["key"]: c["state"] for c in rules.evaluate(_run(5, drives=two), settings.DEFAULTS["resource"])}
    assert states["disk_load:PhysicalDrive1"] == "alert" and states["disk_load:PhysicalDrive0"] == "ok"


def test_an_unread_load_is_unknown_never_ok():
    row = _drive(busy=None, queue=None)
    assert _state(_run(5, drives={"E:": row}))["state"] == "unknown"
    assert _state(_run(5, drives={"E:": _drive(lat="unknown")}))["state"] == "unknown"


def test_load_is_not_judged_where_latency_is_unsupported():
    conds = rules.evaluate(_run(5, drives={"/": _drive(lat="unsupported")}), settings.DEFAULTS["resource"])
    assert not [c for c in conds if c["kind"] == "disk_load"]


def test_overall_ranks_alert_over_watch_over_unknown():
    watching = rules.evaluate(_run(3), settings.DEFAULTS["resource"])
    assert rules.overall(watching) == "watch"
    assert rules.overall(rules.evaluate(_run(5), settings.DEFAULTS["resource"])) == "alert"
    assert [c["state"] for c in rules.worst_first(watching)] == ["watch"]


# ------------------------------------------------------------ the alert and verdict


class _HotProbe:
    def __init__(self):
        self.i = 0

    def prime(self):
        pass

    def sample(self):
        self.i += 1
        s = _sample(self.i, drives=_hot())
        s["ts"] = T0 + self.i * 60.0
        return s


def _resource_watch(tmp_path):
    sent = []
    w = resource_watch.ResourceWatch(_HotProbe(), ResourceStore(tmp_path / "r.sqlite3"),
                                     lambda text, cfg: (sent.append(text) or True, None))
    w.sent = sent
    return w


def test_the_warning_sends_nothing_and_the_alert_sends_one_line(tmp_path):
    w = _resource_watch(tmp_path)
    for _ in range(LOAD["watch_sustain_samples"]):
        out = w.tick(_cfg())
    assert out["state"] == "watch" and w.sent == []
    for _ in range(LOAD["alert_sustain_samples"] - LOAD["watch_sustain_samples"]):
        out = w.tick(_cfg())
    assert out["state"] == "alert" and len(w.sent) == 1 and "disk" in w.sent[0]
    w.tick(_cfg())
    assert len(w.sent) == 1


def test_the_payload_carries_busy_queue_and_latency_per_drive_letter(tmp_path):
    w = _resource_watch(tmp_path)
    out = w.tick(_cfg())
    drive = out["drives"][0]
    assert (drive["drive"], drive["busy_percent"], drive["queue"], drive["read_ms"]) == ("E:", 100.0, 25.0, 240.0)
    line = w.card_summary()["disks"][0]
    assert line["drives"] == ["E:"] and line["busy_percent"] == 100.0 and line["queue"] == 25.0


def test_a_critical_disk_turns_the_machine_verdict_act_and_alerts(tmp_path, monkeypatch, caplog):
    w = _resource_watch(tmp_path)
    monkeypatch.setattr(resource_watch, "WATCH", w)
    health = watch.HealthWatch()
    for _ in range(LOAD["alert_sustain_samples"]):
        w.tick(_cfg())
    with caplog.at_level(logging.WARNING, logger="src.health.watch"):
        verdict = health.refresh_alerts(now=T0 + 6 * 60.0)
    assert verdict["verdict"] == "act"
    assert "E:" in verdict["reason"] and "queue 25" in verdict["reason"]
    assert any(f["metric"] == "disk_load" and f["level"] == "act" for f in verdict["findings"])
    assert verdict["disks"][0]["drives"] == ["E:"] and verdict["disks"][0]["state"] == "alert"
    assert health.current(now=T0 + 6 * 60.0)["alerts"][0]["kind"] == "act"
    assert "health watch: act" in caplog.text
    card = watch.card_fields(verdict)
    assert card["disks"][0]["busy_percent"] == 100.0 and card["verdict"] == "act"


def test_a_warning_disk_is_a_watch_verdict_and_a_calm_disk_adds_nothing(tmp_path, monkeypatch):
    w = _resource_watch(tmp_path)
    monkeypatch.setattr(resource_watch, "WATCH", w)
    health = watch.HealthWatch()
    for _ in range(LOAD["watch_sustain_samples"]):
        w.tick(_cfg())
    assert health.current(now=T0 + 4 * 60.0)["verdict"] == "watch"
    calm = _resource_watch(tmp_path)
    calm._probe.sample = lambda: {**_sample(1), "ts": T0 + 60.0}
    monkeypatch.setattr(resource_watch, "WATCH", calm)
    calm.tick(_cfg())
    verdict = health.current(now=T0 + 120.0)
    assert not any(f["metric"] == "disk_load" for f in verdict["findings"])
    assert verdict["disks"][0]["state"] == "ok"


def test_a_stale_resource_sample_is_unknown_in_the_verdict_not_quiet(tmp_path, monkeypatch):
    w = _resource_watch(tmp_path)
    monkeypatch.setattr(resource_watch, "WATCH", w)
    w.tick(_cfg())
    block = w.health_block(now=T0 + 60.0 + 10 * 60.0)
    assert block["findings"][0]["level"] == "unknown" and "not sampled" in block["findings"][0]["reason"]
    assert classifier.LEVEL_RANK["unknown"] > classifier.LEVEL_RANK["ok"]


def test_a_watch_with_no_sample_or_switched_off_adds_nothing(tmp_path):
    assert _resource_watch(tmp_path).health_block() is None
    off = _resource_watch(tmp_path)
    off.tick(_cfg())
    off._cfg = {**off._cfg, "enabled": False}
    assert off.health_block(now=T0 + 120.0) is None
