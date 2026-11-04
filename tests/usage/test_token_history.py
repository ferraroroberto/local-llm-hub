"""Unit tests for the cached per-day, per-agent token history.

The sources are replaced with fixture records; nothing reads a real log. The
contracts under test: a read is served from the cache without waiting on a
recalculation, a failed recalculation keeps the last result and flags it
stale, and "never measured" is ``unknown`` / ``not_measured``, never zero.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from src.usage import code_usage, code_usage_history, pi_usage, token_history
from src.usage.usage_common import UsageRecord


def _rec(vendor, day, *, i=0, o=0, cw=0, cr=0, model="claude-opus-4-8",
         project="E--automation-demo", requests=1) -> UsageRecord:
    y, m, d = (int(x) for x in day.split("-"))
    return UsageRecord(
        session_id=f"{vendor}-{day}", project_key=project, project_name="demo",
        model=model, ts=datetime(y, m, d, 9, tzinfo=timezone.utc),
        input_tokens=i, output_tokens=o, cache_creation_tokens=cw,
        cache_read_tokens=cr, vendor=vendor, requests=requests,
    )


@pytest.fixture
def sources(monkeypatch):
    """Hermetic sources: ``sources.native`` / ``.pi`` / ``.otel`` are the
    record lists each reader returns."""
    class S:
        native: list = []
        pi: list = []
        otel: list = []

    monkeypatch.setattr(code_usage, "_gather_records", lambda vendor="all": list(S.native))
    monkeypatch.setattr(pi_usage, "all_records", lambda: list(S.pi))
    monkeypatch.setattr(code_usage, "_otel_delta_records", lambda records: list(S.otel))
    # Freeze the wall clock inside 2026-06-10 so "today" is stable.
    clock = {"now": datetime(2026, 6, 10, 12, tzinfo=timezone.utc).timestamp()}
    monkeypatch.setattr(token_history, "_wall", lambda: clock["now"])
    monkeypatch.setattr(token_history, "today_utc", lambda: datetime.fromtimestamp(clock["now"], tz=timezone.utc).date())
    S.clock = clock
    return S


def _cell(body, day, vendor):
    return next(d for d in body["days"] if d["date"] == day)["vendors"][vendor]


def test_cold_read_is_empty_unknown_and_never_zero(sources):
    body = token_history.get_history("2026-06-08", "2026-06-10")

    assert body["cache"]["state"] == "empty"
    assert body["cache"]["computed_at"] is None
    assert body["cache"]["age_seconds"] is None
    assert [d["date"] for d in body["days"]] == ["2026-06-08", "2026-06-09", "2026-06-10"]
    for day in body["days"]:
        assert day["vendors"]["claude"] == {"state": "unknown"}
        assert day["vendors"]["pi"] == {"state": "unknown"}
        assert day["vendors"]["grok"] == {"state": "not_measured"}


def test_read_is_served_from_cache_with_its_age(sources):
    sources.native = [
        _rec("claude", "2026-06-09", i=10, o=20, cw=5, cr=1000, requests=2),
        _rec("claude", "2026-06-09", i=1, o=2, model="claude-sonnet-5-5"),
    ]
    sources.pi = [_rec("pi", "2026-06-10", i=7, o=9, cr=300, cw=40, model="gpt-5.5")]
    assert token_history._refresh_body() is True

    # Sources change after the compute: the cache must not notice.
    sources.native = []
    sources.pi = []
    sources.clock["now"] += 90

    body = token_history.get_history("2026-06-09", "2026-06-10")

    assert body["cache"]["state"] == "ok"
    assert body["cache"]["stale"] is False
    assert body["cache"]["age_seconds"] == 90.0
    claude = _cell(body, "2026-06-09", "claude")
    assert claude["state"] == "measured"
    assert (claude["input_tokens"], claude["output_tokens"]) == (11, 22)
    assert (claude["cache_creation_tokens"], claude["cache_read_tokens"]) == (5, 1000)
    assert claude["tokens_in"] == 16  # input + cache writes, never cache reads
    assert claude["requests"] == 3
    assert {m["model"] for m in claude["models"]} == {"Opus", "Sonnet"}
    assert claude["projects"][0]["project"] == "demo"
    pi = _cell(body, "2026-06-10", "pi")
    assert (pi["input_tokens"], pi["output_tokens"], pi["cache_read_tokens"]) == (7, 9, 300)


def test_codex_input_excludes_its_cached_subset(sources):
    sources.native = [_rec("codex", "2026-06-10", i=1000, o=50, cr=900, model="gpt-5.5")]
    token_history._refresh_body()

    codex = _cell(token_history.get_history("2026-06-10", "2026-06-10"), "2026-06-10", "codex")

    assert codex["input_tokens"] == 100
    assert codex["cache_read_tokens"] == 900


def test_unknown_before_first_day_zero_after_and_unknown_in_the_future(sources):
    sources.pi = [_rec("pi", "2026-06-08", i=5, o=5)]
    token_history._refresh_body()

    body = token_history.get_history("2026-06-06", "2026-06-12")

    assert _cell(body, "2026-06-07", "pi") == {"state": "unknown"}  # before first data
    assert _cell(body, "2026-06-08", "pi")["state"] == "measured"
    quiet = _cell(body, "2026-06-09", "pi")  # measured, nothing used
    assert quiet["state"] == "measured" and quiet["tokens_in"] == 0 and quiet["requests"] == 0
    assert _cell(body, "2026-06-11", "pi") == {"state": "unknown"}  # after the compute
    assert _cell(body, "2026-06-09", "codex") == {"state": "unknown"}  # never seen


def test_grok_is_not_measured_even_with_records_of_its_name(sources):
    sources.native = [_rec("grok", "2026-06-10", i=5, o=5)]
    token_history._refresh_body()

    body = token_history.get_history("2026-06-10", "2026-06-10")

    assert _cell(body, "2026-06-10", "grok") == {"state": "not_measured"}
    grok = next(v for v in body["vendors"] if v["vendor"] == "grok")
    assert grok["state"] == "not_measured"


def test_bridged_otel_source_stays_a_separate_agent(sources):
    sources.native = [_rec("claude", "2026-06-10", i=10, o=10)]
    sources.otel = [_rec("claude", "2026-06-10", i=4, o=6, requests=0, project="otel-untracked")]
    token_history._refresh_body()

    body = token_history.get_history("2026-06-10", "2026-06-10")

    assert _cell(body, "2026-06-10", "claude")["input_tokens"] == 10
    bridged = _cell(body, "2026-06-10", "claude_bridged")
    assert (bridged["input_tokens"], bridged["output_tokens"]) == (4, 6)


def test_read_does_not_wait_for_a_running_recalculation(sources, monkeypatch):
    sources.native = [_rec("claude", "2026-06-10", i=1, o=1)]
    token_history._refresh_body()

    entered, release = threading.Event(), threading.Event()

    def slow():
        entered.set()
        assert release.wait(timeout=10)
        return {}

    monkeypatch.setattr(token_history, "_compute_days", slow)
    worker = threading.Thread(target=token_history._refresh_body)
    worker.start()
    try:
        assert entered.wait(timeout=10)
        body = token_history.get_history("2026-06-10", "2026-06-10", refresh=True)
        assert body["cache"]["refreshing"] is True
        assert _cell(body, "2026-06-10", "claude")["input_tokens"] == 1  # the old result
        assert token_history._refresh_body() is False  # single-flight
    finally:
        release.set()
        worker.join(timeout=10)
    assert token_history.get_history("2026-06-10", "2026-06-10")["cache"]["refreshing"] is False


def test_failed_recalculation_keeps_the_old_result_and_flags_it_stale(sources, monkeypatch):
    sources.native = [_rec("claude", "2026-06-10", i=8, o=2)]
    assert token_history._refresh_body() is True
    real_compute = token_history._compute_days

    def boom():
        raise RuntimeError("transcript store unreadable")

    monkeypatch.setattr(token_history, "_compute_days", boom)
    sources.clock["now"] += 60
    assert token_history._refresh_body() is False

    body = token_history.get_history("2026-06-10", "2026-06-10")
    assert body["cache"]["state"] == "stale"
    assert body["cache"]["stale"] is True
    assert "transcript store unreadable" in body["cache"]["last_error"]
    assert body["cache"]["age_seconds"] == 60.0  # still the earlier result
    assert _cell(body, "2026-06-10", "claude")["input_tokens"] == 8

    # A later success clears the flag.
    monkeypatch.setattr(token_history, "_compute_days", real_compute)
    assert token_history._refresh_body() is True
    assert token_history.get_history("2026-06-10", "2026-06-10")["cache"]["state"] == "ok"


def test_failed_first_recalculation_is_empty_with_the_reason(sources, monkeypatch):
    monkeypatch.setattr(token_history, "_compute_days",
                        lambda: (_ for _ in ()).throw(OSError("disk gone")))
    assert token_history._refresh_body() is False

    body = token_history.get_history("2026-06-10", "2026-06-10")

    assert body["cache"]["state"] == "empty"
    assert "disk gone" in body["cache"]["last_error"]
    assert _cell(body, "2026-06-10", "claude") == {"state": "unknown"}


def test_pruned_days_survive_a_later_recalculation(sources):
    sources.pi = [_rec("pi", "2026-05-01", i=50, o=50), _rec("pi", "2026-06-10", i=1, o=1)]
    token_history._refresh_body()

    sources.pi = [_rec("pi", "2026-06-10", i=1, o=1)]  # May's log was pruned
    token_history._refresh_body()

    may = _cell(token_history.get_history("2026-05-01", "2026-05-01"), "2026-05-01", "pi")
    assert may["input_tokens"] == 50


def test_pi_is_not_folded_into_the_code_tab_history(sources):
    sources.native = [_rec("claude", "2026-06-10", i=1, o=1)]
    sources.pi = [_rec("pi", "2026-06-10", i=1, o=1)]
    token_history._refresh_body()

    vendors = {e["vendor"] for e in code_usage_history._load_locked().values()}
    assert vendors == {"claude"}


def test_cache_survives_a_restart(sources, tmp_path):
    sources.pi = [_rec("pi", "2026-06-10", i=3, o=4)]
    token_history._refresh_body()

    token_history._state = None  # a fresh process loads the file

    body = token_history.get_history("2026-06-10", "2026-06-10")
    assert body["cache"]["state"] == "ok"
    assert _cell(body, "2026-06-10", "pi")["input_tokens"] == 3


def test_cold_read_starts_one_background_recalculation(sources, monkeypatch):
    ran = threading.Event()
    monkeypatch.setattr(token_history, "_spawn_enabled", True)
    monkeypatch.setattr(token_history, "_refresh_body", ran.set)

    body = token_history.get_history("2026-06-10", "2026-06-10")

    assert ran.wait(timeout=10)
    assert body["cache"]["state"] == "empty"  # the read itself did not wait


@pytest.mark.parametrize("from_,to", [
    ("2026-06-10", "2026-06-01"),
    ("nope", None),
    ("2024-01-01", "2026-06-10"),
])
def test_bad_ranges_are_rejected(from_, to):
    with pytest.raises(ValueError):
        token_history.parse_range(from_, to)


def test_default_range_is_the_last_30_days(sources):
    start, end = token_history.parse_range(None, None)
    assert end.isoformat() == "2026-06-10"
    assert (end - start).days + 1 == 30
