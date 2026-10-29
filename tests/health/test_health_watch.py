"""Health watch end to end: a real leaking process, the endpoint, the loop.

The leak test spawns a child that opens kernel handles on command and reads
it through the real Windows probe; only the clock is synthetic (each probe
is stamped 10 minutes after the last), so growth per hour is computed exactly
as it is live.
"""

from __future__ import annotations

import asyncio
import copy
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from src import server as server_mod
from src.health import sampler, settings, watch, win_probe
from src.no_window import NO_WINDOW

T0 = 1_800_000_000.0
STEP = 600.0
HANDLES_PER_TICK = 400

_LEAKER = r"""
import ctypes, os, sys
kernel32 = ctypes.WinDLL("kernel32")
kept = []
print(os.getpid(), flush=True)
for line in sys.stdin:
    for _ in range(int(line)):
        kept.append(kernel32.CreateEventW(None, True, False, None))
    print(len(kept), flush=True)
"""


@pytest.fixture()
def fresh_watch():
    watch.WATCH.reset()
    yield watch.WATCH
    watch.WATCH.reset()


def _child_sample(pid: int, i: int):
    rows = [r for r in sampler._windows_processes() if r["pid"] == pid]
    return {"ts": T0 + i * STEP, "platform": "win32", "system": {},
            "probes": {"processes": "ok"}, "processes": rows, "sample_ms": 1.0}


@pytest.mark.skipif(not win_probe.available(), reason="native handle probe is 64-bit Windows only")
def test_a_leaking_process_is_flagged_watch_then_act_and_clears_after_exit(fresh_watch):
    cfg = copy.deepcopy(settings.DEFAULTS)
    # Scaled to a test-sized leak: 400 handles per 10 min = 2,400 an hour.
    cfg["process"]["handles"].update(watch_value=None, act_value=4_000, watch_rate_per_hour=1_000,
                                     act_rate_per_hour=1_000_000, rate_min_value=0)
    child = subprocess.Popen(
        [sys.executable, "-c", _LEAKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, creationflags=NO_WINDOW,
    )
    try:
        # The venv interpreter is a redirector stub; the leaker names its own PID.
        pid = int(child.stdout.readline())
        levels = []
        for i in range(12):
            child.stdin.write(f"{HANDLES_PER_TICK}\n")
            child.stdin.flush()
            child.stdout.readline()
            levels.append(fresh_watch.record(_child_sample(pid, i), cfg)["verdict"])
        assert levels[0] == "ok"
        assert "watch" in levels and "act" in levels
        assert levels.index("watch") < levels.index("act")
        verdict = fresh_watch.current(now=T0 + 11 * STEP)
        finding = verdict["findings"][0]
        assert finding["process"].lower().startswith("python")
        assert finding["rate_per_hour"] == pytest.approx(HANDLES_PER_TICK * 6, rel=0.1)
        assert verdict["fix"] == f"Restart {finding['process']}"
    finally:
        child.stdin.close()
        child.wait(timeout=10)
    cleared = fresh_watch.record(_child_sample(pid, 12), cfg)
    assert cleared["verdict"] == "ok"
    assert [a["kind"] for a in fresh_watch.current(now=T0 + 12 * STEP)["alerts"]] == ["act", "cleared"]


@pytest.mark.skipif(not win_probe.available(), reason="native probes are 64-bit Windows only")
def test_native_process_table_matches_psutil():
    import os

    import psutil

    row = next(r for r in win_probe.process_table() if r["pid"] == os.getpid())
    me = psutil.Process()
    assert row["threads"] == me.num_threads()
    assert abs(row["handles"] - me.num_handles()) < 50
    assert row["name"].lower().startswith("python")


def test_a_live_sample_is_cheap_and_reports_every_probe():
    sample = sampler.take_sample()
    assert sample["sample_ms"] < 2_000
    assert set(sample["probes"]) == {"processes", "performance", "memory", "tcp", "disk"}
    assert sample["processes"]


def test_health_endpoint_and_host_card_report_the_watch(fresh_watch, config_with_example_identity):
    cfg = copy.deepcopy(settings.DEFAULTS)
    fresh_watch.record({"ts": time.time(), "platform": "win32",
                        "system": {"disk_free_percent": 2.0}, "probes": {"disk": "ok"},
                        "processes": [], "sample_ms": 1.0}, cfg)
    client = TestClient(server_mod.app)
    body = client.get("/admin/api/machines/health").json()
    assert body["verdict"] == "act"
    assert body["fix"] == "Free space on the system disk"
    assert body["host_id"]
    assert body["alerts"][0]["kind"] == "act"

    status = client.get("/admin/api/machines/status").json()
    host = next(m for m in status["machines"] if m["is_host"])
    assert host["health"]["verdict"] == "act"
    assert host["health"]["findings"][0]["reason"].startswith("System disk free")


def test_health_endpoint_before_any_sample_is_unknown(fresh_watch):
    body = TestClient(server_mod.app).get("/admin/api/machines/health").json()
    assert body["verdict"] == "unknown"
    assert body["reason"] == "No sample yet"


def test_the_loop_samples_off_the_event_loop(fresh_watch, monkeypatch):
    """A slow sample must not stall the event loop: a ticker keeps running
    while the sample blocks its worker thread."""
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg.update(boot_delay_s=0, interval_s=3600)
    monkeypatch.setattr(settings, "load", lambda path=None: cfg)

    def slow_sample(**_kwargs):
        time.sleep(0.5)
        return {"ts": time.time(), "platform": sys.platform, "system": {}, "probes": {},
                "processes": [], "sample_ms": 500.0}

    monkeypatch.setattr(sampler, "take_sample", slow_sample)

    async def scenario():
        task = asyncio.create_task(watch.run_loop())
        ticks = [time.monotonic()]
        while fresh_watch.current()["samples"] == 0 and time.monotonic() - ticks[0] < 5:
            await asyncio.sleep(0.02)
            ticks.append(time.monotonic())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return max(b - a for a, b in zip(ticks, ticks[1:]))

    assert asyncio.run(scenario()) < 0.3
    assert fresh_watch.current()["samples"] == 1


def test_disabled_watch_never_samples(fresh_watch, monkeypatch):
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg["enabled"] = False
    monkeypatch.setattr(settings, "load", lambda path=None: cfg)
    monkeypatch.setattr(sampler, "take_sample", lambda **_k: pytest.fail("sampled while disabled"))
    asyncio.run(watch.run_loop())
    assert fresh_watch.current()["samples"] == 0
    assert fresh_watch.card_summary()["enabled"] is False
