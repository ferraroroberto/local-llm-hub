"""Unit tests for the weekly coding-agent usage digest and budget alert.

The history is a fixture dict in the shape ``token_history.get_history``
returns and the Telegram transport is a recording stub; nothing reads a real
log or reaches the network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.health import resource_alert
from src.usage import digest

TODAY = date(2026, 6, 10)  # a Wednesday


def _measured(tokens: int, requests: int = 1) -> dict:
    return {"state": "measured", "input_tokens": tokens, "cache_creation_tokens": 0,
            "output_tokens": 0, "cache_read_tokens": 9_999_999, "requests": requests}


def _history(per_day: dict, *, state: str = "ok", age: float = 60.0, vendors=("claude", "codex", "grok")) -> dict:
    """``per_day``: vendor -> {iso day: tokens}; any other day is unknown."""
    days = []
    for back in range(14, -1, -1):
        iso = (TODAY - timedelta(days=back)).isoformat()
        cells = {}
        for v in vendors:
            if v == "grok":
                cells[v] = {"state": "not_measured"}
            elif iso in per_day.get(v, {}):
                cells[v] = _measured(per_day[v][iso])
            else:
                cells[v] = {"state": "unknown"}
        days.append({"date": iso, "vendors": cells})
    return {
        "days": days,
        "cache": {"state": state, "age_seconds": age, "stale": state == "stale"},
        "vendors": [{"vendor": v, "state": "not_measured" if v == "grok" else "measured"} for v in vendors],
    }


def _week(end: date, tokens_per_day: int) -> dict:
    return {(end - timedelta(days=i)).isoformat(): tokens_per_day for i in range(7)}


def _agent(report, vendor):
    return next(a for a in report["agents"] if a["vendor"] == vendor)


def test_windows_are_the_two_complete_weeks_before_today_and_cache_reads_are_left_out():
    yesterday = TODAY - timedelta(days=1)
    per_day = {"claude": {**_week(yesterday, 100), **_week(yesterday - timedelta(days=7), 50)}}
    report = digest.build_report(_history(per_day), TODAY)

    claude = _agent(report, "claude")
    assert claude["current"] == {"tokens": 700, "requests": 7}
    assert claude["previous"] == {"tokens": 350, "requests": 7}
    assert report["start"] == date(2026, 6, 3) and report["end"] == date(2026, 6, 9)


def test_text_shows_percent_change_and_never_zero_for_unmeasured_agents():
    yesterday = TODAY - timedelta(days=1)
    per_day = {"claude": {**_week(yesterday, 100_000), **_week(yesterday - timedelta(days=7), 50_000)}}
    report = digest.build_report(_history(per_day), TODAY)

    text = digest.digest_text(report, 60.0, stale=False)

    assert "claude: 700.0k tokens, 7 requests (+100% vs prior week)" in text
    assert "grok: no token counts at the source" in text
    assert "codex" not in text  # never measured: said nothing, not "0"
    assert "Total: 700.0k tokens" in text
    assert "Warning" not in text


def test_a_week_with_no_prior_data_says_so_and_a_stale_cache_is_flagged():
    yesterday = TODAY - timedelta(days=1)
    report = digest.build_report(_history({"claude": _week(yesterday, 10)}, state="stale", age=7200), TODAY)

    text = digest.digest_text(report, 7200.0, stale=True)

    assert "(no prior week)" in text
    assert "figures are 2 h old" in text


def test_a_week_with_no_measured_day_after_a_measured_prior_week_is_unknown_not_zero():
    prior_end = TODAY - timedelta(days=8)
    report = digest.build_report(_history({"claude": _week(prior_end, 10)}), TODAY)

    assert "claude: unknown this week" in digest.digest_text(report, 1.0, stale=False)


def test_budget_breach_uses_the_trailing_week_and_skips_unlisted_and_unmeasured():
    per_day = {"claude": _week(TODAY, 200), "codex": _week(TODAY, 1)}
    report = digest.build_report(_history(per_day), TODAY)

    breaches = digest.budget_breaches(report, {"claude": 1000, "codex": 1000, "grok": 1})

    assert breaches == [{"vendor": "claude", "tokens": 1400, "limit": 1000}]
    assert digest.budget_text(breaches[0]) == "claude used 1.4k tokens over the last 7 days (budget 1.0k)."


def test_last_scheduled_is_the_most_recent_weekday_hour():
    tz = timezone.utc
    assert digest.last_scheduled(datetime(2026, 6, 10, 12, tzinfo=tz), 0, 8) == datetime(2026, 6, 8, 8, tzinfo=tz)
    assert digest.last_scheduled(datetime(2026, 6, 8, 7, tzinfo=tz), 0, 8) == datetime(2026, 6, 1, 8, tzinfo=tz)
    assert digest.last_scheduled(datetime(2026, 6, 8, 8, tzinfo=tz), 0, 8) == datetime(2026, 6, 8, 8, tzinfo=tz)


def test_config_merges_one_section_deep_and_ignores_comments(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text('{"_comment": "x", "digest": {"hour": 5}, "budget": {"tokens_per_week": {"claude": 7}}}',
                    encoding="utf-8")

    cfg = digest.load_config(path)

    assert cfg["digest"] == {**digest.DEFAULTS["digest"], "hour": 5}
    assert cfg["budget"]["tokens_per_week"] == {"claude": 7}
    assert cfg["budget"]["cooldown_hours"] == 24
    assert "_comment" not in cfg
    assert digest.load_config(tmp_path / "missing.json") == digest.DEFAULTS


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A tick environment: tmp state file, a fixture history, a recording sender."""
    class E:
        sent: list = []
        ok = True
        history = _history({"claude": {**_week(TODAY - timedelta(days=1), 100), **_week(TODAY, 100)}})
        loads = 0

    def fake_send(text, cfg):
        E.sent.append((cfg["category"], text))
        return (E.ok, None if E.ok else "boom")

    def fake_history(cfg, today):
        E.loads += 1
        return None if E.history is None else (E.history, False)

    monkeypatch.setattr(digest, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(resource_alert, "send_via_notify_script", fake_send)
    monkeypatch.setattr(digest, "_history", fake_history)
    E.sent = []
    return E


# Wednesday 2026-06-10 12:00 UTC; the scheduled moment before it is Monday 08:00.
NOW = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)


def _cfg(**budget):
    cfg = digest.load_config(None)
    cfg["budget"] = {**cfg["budget"], **budget}
    return cfg


def test_first_run_seeds_the_baseline_and_sends_nothing(env):
    out = digest.tick(_cfg(), NOW)

    assert out["digest"] == "not-due" and env.sent == [] and env.loads == 0
    assert digest._load_state()["digest_sent_at"] == NOW.timestamp()


def test_digest_goes_out_once_after_the_scheduled_moment(env):
    digest.tick(_cfg(), NOW - timedelta(days=3))  # seeds on the Sunday before
    out = digest.tick(_cfg(), NOW)

    assert out["digest"] == "sent"
    assert [c for c, _ in env.sent] == ["log"]
    assert digest.tick(_cfg(), NOW + timedelta(hours=1))["digest"] == "not-due"
    assert len(env.sent) == 1


def test_a_failed_digest_is_retried_after_the_wait_and_never_recorded_as_sent(env):
    digest.tick(_cfg(), NOW - timedelta(days=3))
    env.ok = False
    assert digest.tick(_cfg(), NOW)["digest"] == "failed"
    assert digest.tick(_cfg(), NOW + timedelta(minutes=1))["digest"] == "not-due"  # retry wait
    env.ok = True
    assert digest.tick(_cfg(), NOW + timedelta(minutes=11))["digest"] == "sent"
    assert len(env.sent) == 2


def test_an_unreadable_history_sends_nothing_and_is_retried(env):
    digest.tick(_cfg(), NOW - timedelta(days=3))
    env.history = None

    out = digest.tick(_cfg(), NOW)

    assert out["history"] == "unknown" and out["digest"] == "not-due" and env.sent == []
    env.history = _history({"claude": _week(TODAY, 1)})
    assert digest.tick(_cfg(), NOW + timedelta(minutes=1))["digest"] == "sent"


def test_budget_alert_fires_once_per_cooldown_and_a_failure_does_not_start_it(env):
    cfg = _cfg(tokens_per_week={"claude": 100})
    digest.tick(cfg, NOW)  # seeds; the alert check itself runs now
    assert env.sent[0][0] == "attention" and "claude used" in env.sent[0][1]

    env.sent.clear()
    assert digest.tick(cfg, NOW + timedelta(hours=7))["alerts"] == []  # inside the 24 h cooldown
    assert env.sent == []
    assert digest.tick(cfg, NOW + timedelta(hours=25))["alerts"] == ["claude"]

    env.sent.clear()
    env.ok = False
    later = NOW + timedelta(days=3)
    assert digest.tick(cfg, later)["alerts"] == []
    env.ok = True
    assert digest.tick(cfg, later + timedelta(hours=7))["alerts"] == ["claude"]


def test_no_budgets_means_no_history_read_between_digests(env):
    digest.tick(_cfg(), NOW)
    digest.tick(_cfg(), NOW + timedelta(hours=7))

    assert env.loads == 0
