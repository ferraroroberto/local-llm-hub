"""Unit tests for src/fleet_placement.py (issue #353).

Desired-state ``{host_id: [model_id, ...]}`` load/save: tolerant load with an
example-file fallback, validated save (unknown host rejected, non-launchable id
dropped), and a path-keyed cache that a test-swapped path busts cleanly.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest  # noqa: E402

from src import fleet_placement as fp  # noqa: E402


def _isolate(monkeypatch, tmp_path, initial=None):
    target = tmp_path / "fleet_placement.json"
    if initial is not None:
        target.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(fp, "DEFAULT_PLACEMENT_PATH", target)
    fp._PLACEMENT_CACHE.clear()
    return target


def test_load_missing_returns_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert fp.load_fleet_placement() == {}


def test_load_roundtrips_a_written_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, {"tower": ["whisper"], "mac-mini-m4": ["parakeet"]})
    got = fp.load_fleet_placement()
    assert got == {"tower": ["whisper"], "mac-mini-m4": ["parakeet"]}


def test_load_tolerant_on_garbage(monkeypatch, tmp_path):
    target = _isolate(monkeypatch, tmp_path)
    target.write_text("{ not json", encoding="utf-8")
    assert fp.load_fleet_placement() == {}


def test_load_coerces_bad_shapes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, {"tower": ["a", 2, None, ""], "bad": "notalist"})
    got = fp.load_fleet_placement()
    assert got["tower"] == ["a", "2"]  # None/"" dropped, ints stringified
    assert "bad" not in got            # non-list value dropped entirely


def test_example_is_not_a_fallback(monkeypatch, tmp_path):
    # Deliberate divergence from startup_profile: an absent live file must NOT
    # fall back to the committed example (that would auto-enforce a placement
    # across other machines). Reconcile stays inert until placement is set.
    live = tmp_path / "fleet_placement.json"
    example = tmp_path / "fleet_placement.example.json"
    example.write_text(json.dumps({"tower": ["piper"]}), encoding="utf-8")
    monkeypatch.setattr(fp, "DEFAULT_PLACEMENT_PATH", live)
    monkeypatch.setattr(fp, "EXAMPLE_PLACEMENT_PATH", example)
    fp._PLACEMENT_CACHE.clear()
    assert fp.load_fleet_placement() == {}  # example ignored, not seeded


def test_save_validates_and_drops_unlaunchable(monkeypatch, tmp_path):
    target = _isolate(monkeypatch, tmp_path)
    # parakeet is launchable on mac-mini; "not-a-real-id" is not → dropped.
    saved = fp.save_fleet_placement({"mac-mini-m4": ["parakeet", "not-a-real-id"]})
    assert saved == {"mac-mini-m4": ["parakeet"]}
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk == {"mac-mini-m4": ["parakeet"]}


def test_save_dedups_preserving_order(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    saved = fp.save_fleet_placement({"mac-mini-m4": ["parakeet", "qwen", "parakeet"]})
    assert saved["mac-mini-m4"] == ["parakeet", "qwen"]


def test_save_rejects_unknown_host(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        fp.save_fleet_placement({"ghost-host": ["whisper"]})


def test_save_rejects_non_list_placement(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        fp.save_fleet_placement({"tower": "whisper"})


def test_save_busts_cache(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, {"tower": ["whisper"]})
    assert fp.load_fleet_placement() == {"tower": ["whisper"]}  # populates cache
    fp.save_fleet_placement({"tower": ["piper"]})
    assert fp.load_fleet_placement() == {"tower": ["piper"]}    # cache busted
