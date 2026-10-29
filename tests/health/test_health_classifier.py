"""Health-watch verdict rules (src/health/classifier.py).

The classifier is a pure function of the sample history, so every rule is
exercised on synthetic samples spaced like the real 10-minute cadence.
"""

from __future__ import annotations

import copy
import json

import pytest

from src.health import classifier, settings
from src.health.watch import HealthWatch

T0 = 1_800_000_000.0
STEP = 600.0


def _cfg(**overrides):
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg.update(overrides)
    return cfg


def _proc(name, pid=100, **metrics):
    row = {"key": f"{pid}:1", "pid": pid, "name": name, "handles": None,
           "memory_mb": None, "gdi": None, "user": None, "threads": None}
    row.update(metrics)
    return row


def _sample(i, processes=(), system=None, probes=None):
    return {
        "ts": T0 + i * STEP,
        "platform": "win32",
        "system": system if system is not None else {"commit_percent": 50.0, "total_handles": 600_000},
        "probes": probes if probes is not None else {"processes": "ok", "performance": "ok"},
        "processes": list(processes),
        "sample_ms": 30.0,
    }


def _classify(history, cfg=None):
    return classifier.classify(history, cfg or _cfg(), now=history[-1]["ts"] if history else T0)


def test_steady_handle_leak_goes_watch_then_act_and_clears_on_exit():
    """2 k handles per 10 min = 12 k/h: watch once the trend is established,
    act once the act ceiling is passed, cleared once the process is gone,
    with one alert on act and one on clear."""
    cfg = _cfg()
    cfg["process"]["handles"].update(watch_value=None, act_value=100_000)
    watch = HealthWatch()
    levels = []
    for i in range(50):
        handles = 25_000 + i * 2_000
        levels.append(watch.record(_sample(i, [_proc("leaky.exe", handles=handles)]), cfg)["verdict"])
    assert levels[0] == "ok"
    assert levels.index("watch") == 3
    assert levels.index("act") > levels.index("watch")
    acting = watch.current(now=T0 + 49 * STEP)
    assert acting["verdict"] == "act"
    finding = acting["findings"][0]
    assert finding["process"] == "leaky.exe"
    assert finding["rate_per_hour"] == pytest.approx(12_000, rel=0.01)
    assert acting["fix"] == "Restart leaky.exe"
    assert "leaky.exe" in acting["reason"] and "/h" in acting["reason"]

    cleared = watch.record(_sample(50, []), cfg)
    assert cleared["verdict"] == "ok"
    kinds = [a["kind"] for a in watch.current(now=T0 + 50 * STEP)["alerts"]]
    assert kinds == ["act", "cleared"]


def test_rate_alone_reaches_act_before_the_absolute_ceiling():
    """The real case: about 90 k handles an hour. Steady growth that fast is
    act on its own, with the time left to the act ceiling."""
    history = [_sample(i, [_proc("camera.exe", handles=50_000 + i * 15_000)]) for i in range(6)]
    verdict = _classify(history)
    assert verdict["verdict"] == "act"
    finding = verdict["findings"][0]
    assert finding["rate_per_hour"] == pytest.approx(90_000, rel=0.01)
    assert finding["eta_hours"] is not None and finding["eta_hours"] > 0


def test_real_case_system_handles_point_at_the_process_not_a_reboot():
    """3.9 M handles in one process pushing the total to 4.56 M: the fix is
    restarting that process, never a reboot."""
    history = [_sample(0, [_proc("camera.exe", handles=3_900_000)],
                       system={"total_handles": 4_560_000, "commit_percent": 60.0})]
    verdict = _classify(history)
    assert verdict["verdict"] == "act"
    system = next(f for f in verdict["findings"] if f["kind"] == "system")
    assert system["metric"] == "total_handles"
    assert system["fix"] == "Restart camera.exe"
    assert verdict["fix"] == "Restart camera.exe"


def test_handles_spread_across_processes_suggest_reboot():
    procs = [_proc(f"app{i}.exe", pid=i, handles=100_000) for i in range(5)]
    verdict = _classify([_sample(0, procs, system={"total_handles": 4_500_000})])
    assert verdict["verdict"] == "act"
    assert verdict["fix"].startswith("Reboot")


def test_one_step_up_is_not_a_leak():
    """A model load is one jump, then flat: no growth finding."""
    series = [1_000, 1_000, 9_000, 9_000, 9_000, 9_000, 9_000]
    history = [_sample(i, [_proc("llama-server.exe", memory_mb=v)]) for i, v in enumerate(series)]
    assert _classify(history)["verdict"] == "ok"


def test_noisy_flat_series_is_not_a_leak():
    series = [40_000, 41_000, 40_200, 41_500, 40_100, 41_200, 40_300]
    history = [_sample(i, [_proc("busy.exe", handles=v)]) for i, v in enumerate(series)]
    assert _classify(history)["verdict"] == "ok"


def test_too_few_samples_never_compute_a_rate():
    history = [_sample(i, [_proc("new.exe", handles=30_000 + i * 50_000)]) for i in range(3)]
    verdict = _classify(history)
    assert all(f.get("rate_per_hour") is None for f in verdict["findings"])


def test_a_restarted_process_starts_a_new_series():
    """Same name and PID, new create time: the old growth does not carry over."""
    history = [_sample(i, [_proc("app.exe", handles=30_000 + i * 20_000)]) for i in range(5)]
    reborn = _proc("app.exe", handles=30_000)
    reborn["key"] = "100:2"
    history.append(_sample(5, [reborn]))
    assert _classify(history)["verdict"] == "ok"


def test_reboot_only_process_suggests_reboot():
    history = [_sample(0, [_proc("System", pid=4, handles=1_200_000)])]
    verdict = _classify(history)
    assert verdict["verdict"] == "act"
    assert verdict["fix"] == "Reboot: System cannot be restarted on its own"


@pytest.mark.parametrize(
    "metric, value, level, fix_start",
    [
        ("commit_percent", 90.0, "watch", "Reboot"),
        ("commit_percent", 97.0, "act", "Reboot"),
        ("ram_available_percent", 8.0, "watch", "Close"),
        ("ram_available_percent", 3.0, "act", "Close"),
        ("nonpaged_pool_percent", 11.0, "act", "Reboot"),
        ("paged_pool_percent", 16.0, "watch", "Reboot"),
        ("total_handles", 2_500_000, "watch", "Reboot"),
        ("tcp_time_wait", 12_000, "act", "No reboot"),
        ("ephemeral_ports_percent", 60.0, "watch", "No reboot"),
        ("disk_free_percent", 4.0, "act", "Free space"),
    ],
)
def test_system_thresholds(metric, value, level, fix_start):
    verdict = _classify([_sample(0, system={metric: value})])
    assert verdict["verdict"] == level
    assert verdict["fix"].startswith(fix_start)


def test_system_values_inside_limits_are_ok():
    system = {"commit_percent": 60.8, "ram_available_percent": 50.7, "nonpaged_pool_percent": 2.9,
              "paged_pool_percent": 5.5, "total_handles": 650_000, "tcp_time_wait": 626,
              "ephemeral_ports_percent": 4.2, "disk_free_percent": 35.2}
    assert _classify([_sample(0, system=system)])["verdict"] == "ok"


def test_commit_held_by_one_process_suggests_restarting_it():
    system = {"commit_percent": 96.0, "commit_gb": 120.0}
    verdict = _classify([_sample(0, [_proc("hog.exe", memory_mb=90_000)], system=system)])
    assert verdict["fix"] == "Restart hog.exe"


def test_failed_probe_is_unknown_never_ok():
    verdict = _classify([_sample(0, probes={"processes": "ok", "tcp": "failed"})])
    assert verdict["verdict"] == "unknown"
    assert "tcp" in verdict["reason"]


def test_unsupported_and_denied_probes_do_not_block_ok():
    verdict = _classify([_sample(0, probes={"performance": "unsupported", "tcp": "denied"})])
    assert verdict["verdict"] == "ok"


def test_a_known_problem_outranks_a_failed_probe():
    verdict = _classify([_sample(0, system={"disk_free_percent": 2.0}, probes={"tcp": "failed"})])
    assert verdict["verdict"] == "act"
    assert {f["level"] for f in verdict["findings"]} == {"act", "unknown"}


def test_no_sample_and_stale_sample_are_unknown():
    cfg = _cfg()
    assert classifier.classify([], cfg, now=T0)["verdict"] == "unknown"
    stale = classifier.classify([_sample(0)], cfg, now=T0 + 4 * cfg["interval_s"])
    assert stale["verdict"] == "unknown"
    assert "min old" in stale["reason"]


def test_watch_current_goes_unknown_when_sampling_stops():
    cfg = _cfg()
    watch = HealthWatch()
    watch.record(_sample(0), cfg)
    assert watch.current(now=T0 + 60)["verdict"] == "ok"
    assert watch.current(now=T0 + 4 * cfg["interval_s"])["verdict"] == "unknown"


def test_history_is_trimmed_to_its_window():
    cfg = _cfg(history_hours=1)
    watch = HealthWatch()
    for i in range(20):
        watch.record(_sample(i), cfg)
    assert watch.current(now=T0 + 19 * STEP)["samples"] == 7


def test_committed_config_matches_the_documented_defaults():
    """config/health_watch.json is the documented threshold table; every key
    it carries is one the code reads, and every metric has a label."""
    data = json.loads(settings.DEFAULT_PATH.read_text(encoding="utf-8"))
    keys = {k for k in data if not k.startswith("_")}
    assert keys == set(settings.DEFAULTS)
    assert set(data["system"]) == set(classifier.SYSTEM_METRICS)
    assert set(data["process"]) == set(classifier.PROCESS_UNITS)
    assert settings.load() == settings.DEFAULTS


def test_partial_config_overrides_only_what_it_names(tmp_path):
    path = tmp_path / "health_watch.json"
    path.write_text(json.dumps({"interval_s": 60, "process": {"handles": {"act_value": 5}}}), encoding="utf-8")
    cfg = settings.load(path)
    assert cfg["interval_s"] == 60
    assert cfg["process"]["handles"]["act_value"] == 5
    assert cfg["process"]["handles"]["watch_value"] == settings.DEFAULTS["process"]["handles"]["watch_value"]
    assert cfg["system"] == settings.DEFAULTS["system"]


def test_unreadable_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "health_watch.json"
    path.write_text("{not json", encoding="utf-8")
    assert settings.load(path) == settings.DEFAULTS
