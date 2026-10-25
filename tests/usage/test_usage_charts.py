"""Trend-chart bucketing (#751): daily bars for Day/Week/Month, weekly or
monthly bars for All, never more than a phone's worth of bars where the
window allows it."""

from __future__ import annotations

from datetime import date, datetime, timezone

from src import usage_charts, usage_common

TODAY = date(2026, 10, 12)  # a Monday


def _r(day: date, model: str = "claude-sonnet-5", requests: int = 1) -> usage_common.UsageRecord:
    return usage_common.UsageRecord(
        session_id="s", project_key="k", project_name="k", model=model,
        ts=datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc),
        input_tokens=10, output_tokens=5, cache_creation_tokens=2,
        cache_read_tokens=3, requests=requests,
    )


def _labels(series: list) -> list:
    return [b["label"] for b in series]


def test_day_week_month_are_daily_bars_over_their_window():
    for period, span, first in (("today", 14, "Sep 29"), ("week", 7, "Oct 6"), ("month", 30, "Sep 13")):
        series = usage_charts.build_time_series([], period, TODAY)
        assert len(series) == span, period
        assert series[0]["label"] == first, period
        assert series[-1]["label"] == "Oct 12", period
        assert len(series) <= usage_charts.MAX_CHART_BARS


def test_week_keeps_its_seven_days_and_drops_the_eighth():
    series = usage_charts.build_time_series(
        [_r(date(2026, 10, 6)), _r(date(2026, 10, 5)), _r(TODAY, "gpt-5.5")], "week", TODAY,
    )
    by_label = {b["label"]: b["models"] for b in series}
    assert by_label["Oct 6"]["Sonnet"] == {
        "input_tokens": 12, "output_tokens": 5, "cache_read_tokens": 3, "requests": 1,
    }
    assert list(by_label["Oct 12"]) == ["GPT-5.5"]
    assert "Oct 5" not in by_label
    assert sum(len(m) for m in by_label.values()) == 2


def test_all_is_empty_without_records():
    assert usage_charts.build_time_series([], "all", TODAY) == []


def test_all_draws_weekly_bars_from_the_first_record():
    # First record on a Thursday three weeks back: its Monday opens the axis.
    series = usage_charts.build_time_series(
        [_r(date(2026, 9, 24)), _r(date(2026, 9, 25)), _r(TODAY)], "all", TODAY,
    )
    assert _labels(series) == ["Sep 21", "Sep 28", "Oct 5", "Oct 12"]
    assert series[0]["models"]["Sonnet"]["requests"] == 2
    assert series[1]["models"] == {}


def test_all_switches_to_monthly_bars_past_the_bar_budget():
    first = date(2025, 11, 20)  # about 47 weeks back: weekly would pass 31 bars
    series = usage_charts.build_time_series([_r(first), _r(TODAY, requests=4)], "all", TODAY)
    assert _labels(series) == [
        "Nov 2025", "Dec 2025", "Jan 2026", "Feb 2026", "Mar 2026", "Apr 2026",
        "May 2026", "Jun 2026", "Jul 2026", "Aug 2026", "Sep 2026", "Oct 2026",
    ]
    assert series[0]["models"]["Sonnet"]["requests"] == 1
    assert series[-1]["models"]["Sonnet"]["requests"] == 4


def test_all_stays_weekly_at_exactly_the_bar_budget():
    first = date(2026, 3, 16)  # the Monday 30 weeks before TODAY: 31 weekly bars
    series = usage_charts.build_time_series([_r(first)], "all", TODAY)
    assert len(series) == usage_charts.MAX_CHART_BARS
    assert series[0]["label"] == "Mar 16"
    # One week earlier would be 32 bars, so All goes monthly.
    earlier = usage_charts.build_time_series([_r(date(2026, 3, 9))], "all", TODAY)
    assert earlier[0]["label"] == "Mar 2026"
