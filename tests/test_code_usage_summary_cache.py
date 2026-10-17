"""Unit tests for code_usage.get_summary_cached (#811).

A warm summary build walks every live usage record (~745k on the tower), so
the Code tab's 30 s poll paid 4-9 s per call. These tests pin the cache's
contract: callers inside the max age share one build, a summary is never
served older than that age, concurrent callers stampede into one build per
``(period, vendor)``, failures are never cached, and refresh-ahead keeps a
key that is still being polled warm without rebuilding abandoned ones.
"""

from __future__ import annotations

import logging
import threading

import pytest

from src import code_usage


class _Clock:
    """Hand-driven stand-in for ``time.monotonic``."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(code_usage, "_now", c)
    return c


@pytest.fixture
def builds(monkeypatch):
    """Replace the real build with a counter; each build is distinguishable."""
    calls: list = []

    def fake_get_summary(period, vendor):
        calls.append((period, vendor))
        return {"period": period, "vendor": vendor, "build": len(calls)}

    monkeypatch.setattr(code_usage, "get_summary", fake_get_summary)
    return calls


def test_hit_within_max_age_shares_one_build(clock, builds):
    first = code_usage.get_summary_cached("today", "all")
    clock.t += code_usage._SUMMARY_MAX_AGE_S - 1
    second = code_usage.get_summary_cached("today", "all")

    assert builds == [("today", "all")]
    assert first == second == {"period": "today", "vendor": "all", "build": 1}


def test_callers_get_their_own_copy(clock, builds):
    """The router adds an ``agentsview`` block to the body it returns; that
    must never leak into the cached summary another caller is handed."""
    body = code_usage.get_summary_cached("today", "all")
    body["agentsview"] = {"reachable": True}

    assert "agentsview" not in code_usage.get_summary_cached("today", "all")


def test_rebuilds_once_max_age_passes(clock, builds):
    code_usage.get_summary_cached("today", "all")
    clock.t += code_usage._SUMMARY_MAX_AGE_S
    body = code_usage.get_summary_cached("today", "all")

    assert len(builds) == 2
    assert body["build"] == 2


def test_max_age_is_under_the_spa_poll():
    """A cached summary must never be older than one Code-tab poll (30 s)."""
    assert code_usage._SUMMARY_MAX_AGE_S < 30
    assert code_usage._SUMMARY_REFRESH_S < code_usage._SUMMARY_MAX_AGE_S


def test_each_period_vendor_pair_builds_separately(clock, builds):
    code_usage.get_summary_cached("today", "all")
    code_usage.get_summary_cached("week", "all")
    code_usage.get_summary_cached("today", "claude")
    code_usage.get_summary_cached("today", "all")

    assert builds == [("today", "all"), ("week", "all"), ("today", "claude")]


def test_concurrent_callers_share_one_build(monkeypatch):
    """Overlapping polls (two tabs, the design-review walk) must not each
    start a multi-second build: one lock per key, late arrivals reuse it."""
    started = threading.Event()
    release = threading.Event()
    calls: list = []

    def slow_get_summary(period, vendor):
        calls.append((period, vendor))
        started.set()
        assert release.wait(5), "build never released"
        return {"period": period, "vendor": vendor}

    monkeypatch.setattr(code_usage, "get_summary", slow_get_summary)

    results: list = []

    def caller() -> None:
        results.append(code_usage.get_summary_cached("today", "all"))

    threads = [threading.Thread(target=caller) for _ in range(5)]
    threads[0].start()
    assert started.wait(5)
    for t in threads[1:]:
        t.start()
    release.set()
    for t in threads:
        t.join(5)

    assert calls == [("today", "all")]
    assert results == [{"period": "today", "vendor": "all"}] * 5


def test_failed_build_is_not_cached(clock, monkeypatch):
    attempts: list = []

    def flaky(period, vendor):
        attempts.append(period)
        if len(attempts) == 1:
            raise RuntimeError("transcript store unreadable")
        return {"period": period, "vendor": vendor}

    monkeypatch.setattr(code_usage, "get_summary", flaky)

    with pytest.raises(RuntimeError):
        code_usage.get_summary_cached("today", "all")
    assert code_usage.get_summary_cached("today", "all") == {
        "period": "today", "vendor": "all",
    }
    assert len(attempts) == 2


def test_refresh_ahead_rebuilds_a_polled_key(clock, builds):
    """A key polled every 30 s would always miss a cache younger than 30 s;
    refresh-ahead rebuilds it in the background so the next poll is a hit."""
    code_usage.get_summary_cached("today", "all")
    clock.t += code_usage._SUMMARY_REFRESH_S
    code_usage._refresh_due_summaries()

    assert len(builds) == 2
    clock.t += 30 - code_usage._SUMMARY_REFRESH_S  # the next SPA poll
    assert code_usage.get_summary_cached("today", "all")["build"] == 2
    assert len(builds) == 2


def test_refresh_ahead_skips_fresh_and_abandoned_keys(clock, builds):
    code_usage.get_summary_cached("today", "all")
    code_usage._refresh_due_summaries()  # still fresh
    assert len(builds) == 1

    clock.t += code_usage._SUMMARY_WATCH_S + 1  # nobody polled it since
    code_usage._refresh_due_summaries()
    assert len(builds) == 1


def test_failed_refresh_ahead_backs_off(clock, monkeypatch):
    """A broken source must not turn the 1 s refresh tick into a 1 s rebuild
    loop; the cached summary still ages out for callers on its own."""
    attempts: list = []

    def build(period, vendor):
        attempts.append(period)
        if len(attempts) > 1:
            raise RuntimeError("transcript store unreadable")
        return {"period": period}

    monkeypatch.setattr(code_usage, "get_summary", build)
    code_usage.get_summary_cached("today", "all")
    clock.t += code_usage._SUMMARY_REFRESH_S
    code_usage._refresh_due_summaries()
    clock.t += 1
    code_usage._refresh_due_summaries()
    assert len(attempts) == 2

    clock.t += code_usage._SUMMARY_REFRESH_S  # back-off over: one retry
    code_usage._refresh_due_summaries()
    assert len(attempts) == 3


def test_hit_and_rebuild_log_distinct_debug_lines(clock, builds, caplog):
    caplog.set_level(logging.DEBUG, logger=code_usage.__name__)
    code_usage.get_summary_cached("today", "all")
    code_usage.get_summary_cached("today", "all")

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("rebuilt" in m for m in messages), messages
    assert any("served from cache" in m for m in messages), messages


@pytest.fixture
def prewarm_on(monkeypatch):
    """Switch the test-off refresher flag back on, without the real thread."""
    monkeypatch.setattr(code_usage, "_refresher_enabled", True)
    monkeypatch.setattr(code_usage, "_start_refresher", lambda: None)


def test_prewarm_serves_the_first_default_request_from_cache(clock, builds, prewarm_on):
    """A fresh process builds the Code tab's opening key at start (#826), so the
    first request after a restart pays no multi-second cold build."""
    assert code_usage.prewarm_default_summary() is True
    assert builds == [("today", "all")]

    clock.t += 3  # the hub has been up a few seconds when the tab opens
    first = code_usage.get_summary_cached("today", "all")

    assert builds == [("today", "all")]  # still the one prewarm build
    assert first["build"] == 1


def test_prewarm_rebuilds_when_the_cold_build_outlasts_the_max_age(clock, monkeypatch, prewarm_on):
    """A restarted hub's first build parses every transcript (~50 s) — already
    older than the max age when it lands. Prewarm builds once more off the
    warm parser caches so the first request still finds a fresh entry."""
    calls: list = []

    def slow_then_fast(period, vendor):
        calls.append((period, vendor))
        if len(calls) == 1:
            clock.t += code_usage._SUMMARY_MAX_AGE_S * 2  # the cold parse
        return {"build": len(calls)}

    monkeypatch.setattr(code_usage, "get_summary", slow_then_fast)

    assert code_usage.prewarm_default_summary() is True
    assert len(calls) == 2

    assert code_usage.get_summary_cached("today", "all") == {"build": 2}
    assert len(calls) == 2  # the first request was a cache hit


def test_prewarm_only_builds_the_default_key(clock, builds, prewarm_on):
    code_usage.prewarm_default_summary()
    code_usage.get_summary_cached("month", "all")

    assert builds == [("today", "all"), ("month", "all")]


def test_prewarm_logs_an_info_breadcrumb(clock, builds, prewarm_on, caplog):
    caplog.set_level(logging.INFO, logger=code_usage.__name__)
    code_usage.prewarm_default_summary()

    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("prewarmed" in m and "today/all" in m for m in infos), infos


def test_failed_prewarm_warns_and_caches_nothing(clock, monkeypatch, prewarm_on, caplog):
    def boom(period, vendor):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(code_usage, "get_summary", boom)
    caplog.set_level(logging.INFO, logger=code_usage.__name__)

    assert code_usage.prewarm_default_summary() is False  # never raises

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("prewarm" in m and "disk gone" in m for m in warnings), warnings
    assert not any("prewarmed" in r.getMessage() for r in caplog.records)

    # The first real request still builds (and reports) for itself.
    monkeypatch.setattr(code_usage, "get_summary", lambda p, v: {"build": "real"})
    assert code_usage.get_summary_cached("today", "all") == {"build": "real"}


def test_prewarm_is_off_while_the_cache_is_disabled(builds):
    """The suite-wide fixture turns the background machinery off; hub startup
    in a TestClient must not parse the real session logs."""
    assert code_usage.prewarm_default_summary() is False
    assert builds == []


def test_recent_sessions_keep_iso_timestamps_newest_first(monkeypatch):
    """The build-cost cut in the recent-sessions loop (#811) compares
    datetimes instead of strings; the payload must not change shape."""
    from datetime import datetime, timezone

    from src import codex_usage, copilot_usage
    from src import agentsview_usage as av
    from src.usage_common import UsageRecord

    def rec(sid: str, hour: int) -> UsageRecord:
        return UsageRecord(
            session_id=sid, project_key="E--automation-demo", project_name="demo",
            model="claude-fable-5",
            ts=datetime(2026, 10, 17, hour, tzinfo=timezone.utc),
            input_tokens=1, output_tokens=1,
            cache_creation_tokens=0, cache_read_tokens=0,
        )

    records = [rec("old", 9), rec("new", 8), rec("new", 11), rec("old", 7)]
    monkeypatch.setattr(code_usage, "_claude_records", lambda: records)
    monkeypatch.setattr(codex_usage, "all_records", lambda: [])
    monkeypatch.setattr(copilot_usage, "all_records", lambda: [])
    monkeypatch.setattr(av, "all_records", lambda: [])

    sessions = code_usage.get_summary("all", "claude")["recent_sessions"]

    assert [(s["session_id"], s["first_ts"], s["last_ts"], s["requests"]) for s in sessions] == [
        ("new", "2026-10-17T08:00:00+00:00", "2026-10-17T11:00:00+00:00", 2),
        ("old", "2026-10-17T07:00:00+00:00", "2026-10-17T09:00:00+00:00", 2),
    ]
