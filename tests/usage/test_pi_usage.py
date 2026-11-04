"""Unit tests for the Pi usage parser.

Each test writes a synthetic session file into a temp dir and points the
parser at it, so no real ``~/.pi`` tree is read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.usage import pi_usage


def _write(path: Path, lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(o) for o in lines) + "\n", encoding="utf-8")


def _assistant(ts: str, usage: dict | None, model: str = "demo-model") -> dict:
    msg: dict = {"role": "assistant", "model": model, "content": []}
    if usage is not None:
        msg["usage"] = usage
    return {"type": "message", "id": ts, "timestamp": ts, "message": msg}


@pytest.fixture
def pi_dir(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(pi_usage, "_PI_SESSIONS_DIR", sessions)
    pi_usage._file_cache.clear()
    return sessions


def test_maps_usage_fields_per_assistant_turn(pi_dir):
    _write(pi_dir / "--E--automation-demo--" / "2026-06-01T10-00-00Z_a.jsonl", [
        {"type": "session", "id": "sess-1", "cwd": "E:\\automation\\demo"},
        {"type": "message", "message": {"role": "user", "content": "hi"}},
        _assistant("2026-06-01T10:00:05.000Z",
                   {"input": 7, "output": 11, "cacheRead": 300, "cacheWrite": 40,
                    "totalTokens": 358}),
        _assistant("2026-06-01T10:01:00.000Z",
                   {"input": 3, "output": 5, "cacheRead": 0, "cacheWrite": 0}),
    ])

    records = pi_usage.all_records()

    assert len(records) == 2
    first = records[0]
    assert first.vendor == "pi"
    assert first.session_id == "sess-1"
    assert first.project_key == "E--automation-demo"
    assert first.project_name == "demo"
    assert first.model == "demo-model"
    # cacheRead stays its own field, never folded into input.
    assert (first.input_tokens, first.output_tokens) == (7, 11)
    assert first.cache_read_tokens == 300
    assert first.cache_creation_tokens == 40
    assert sum(r.input_tokens for r in records) == 10
    assert first.ts.isoformat() == "2026-06-01T10:00:05+00:00"


def test_skips_turns_without_usage_and_non_assistant_rows(pi_dir):
    _write(pi_dir / "--p--" / "s.jsonl", [
        {"type": "session", "id": "s", "cwd": "E:\\automation\\demo"},
        _assistant("2026-06-01T10:00:00Z", None),
        {"type": "message", "message": {"role": "toolResult", "usage": {"input": 99}}},
        {"type": "custom", "message": {"role": "assistant", "usage": {"input": 99}}},
        _assistant("2026-06-01T10:00:01Z", {"input": 1, "output": 2}),
    ])

    records = pi_usage.all_records()

    assert [(r.input_tokens, r.output_tokens) for r in records] == [(1, 2)]


def test_corrupt_lines_and_missing_dir_are_tolerated(pi_dir):
    assert pi_usage.all_records() == []  # dir does not exist yet

    f = pi_dir / "--p--" / "s.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(
        "not json\n"
        + json.dumps(_assistant("2026-06-01T10:00:00Z", {"input": 4, "output": 6}))
        + "\n",
        encoding="utf-8",
    )

    records = pi_usage.all_records()

    assert len(records) == 1
    assert records[0].project_key == "pi"  # no session header: unattributed
    assert records[0].project_name == "(unknown)"
