"""Unit tests for src/startup_profile.py (issue #265).

Covers the tolerant load contract (missing/unparseable file -> defaults),
atomic save + validation, cache invalidation on save, and cache correctness
across a swapped DEFAULT_PROFILE_PATH (the pattern host_profile._load_config
already relies on for test isolation).
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

import pytest  # noqa: E402

from src import startup_profile as sp  # noqa: E402


def test_missing_file_returns_defaults(tmp_path, monkeypatch):
    # Both the live file and the example template absent → pure defaults.
    monkeypatch.setattr(sp, "DEFAULT_PROFILE_PATH", tmp_path / "does-not-exist.json")
    monkeypatch.setattr(sp, "EXAMPLE_PROFILE_PATH", tmp_path / "no-example.json")
    profile = sp.load_startup_profile()
    assert profile == sp.StartupProfile()


def test_falls_back_to_example_when_live_file_absent(tmp_path, monkeypatch):
    """Fresh clone (no live file) reads the committed example template (#304)."""
    example = tmp_path / "startup_profile.example.json"
    example.write_text(json.dumps({
        "docker": False,
        "mac_mini_sync": False,
        "models": ["orpheus"],
    }), encoding="utf-8")
    monkeypatch.setattr(sp, "DEFAULT_PROFILE_PATH", tmp_path / "startup_profile.json")
    monkeypatch.setattr(sp, "EXAMPLE_PROFILE_PATH", example)

    profile = sp.load_startup_profile()
    assert profile.docker is False
    assert profile.mac_mini_sync is False
    assert profile.models == ["orpheus"]


def test_explicit_path_ignores_example_fallback(tmp_path, monkeypatch):
    """An explicit (test) path is honoured verbatim — never the example."""
    example = tmp_path / "startup_profile.example.json"
    example.write_text(json.dumps({"models": ["orpheus"]}), encoding="utf-8")
    monkeypatch.setattr(sp, "EXAMPLE_PROFILE_PATH", example)
    profile = sp.load_startup_profile(str(tmp_path / "missing.json"))
    assert profile == sp.StartupProfile()


def test_unparseable_file_returns_defaults(tmp_path, monkeypatch):
    target = tmp_path / "startup_profile.json"
    target.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(sp, "DEFAULT_PROFILE_PATH", target)
    profile = sp.load_startup_profile()
    assert profile == sp.StartupProfile()


def test_loads_committed_shape(tmp_path, monkeypatch):
    target = tmp_path / "startup_profile.json"
    target.write_text(json.dumps({
        "docker": False,
        "langfuse": True,
        "mac_mini_sync": False,
        "models": ["qwen35_4b", "piper"],
    }), encoding="utf-8")
    monkeypatch.setattr(sp, "DEFAULT_PROFILE_PATH", target)
    profile = sp.load_startup_profile()
    assert profile.docker is False
    assert profile.langfuse is True
    assert profile.mac_mini_sync is False
    assert profile.models == ["qwen35_4b", "piper"]


def test_cache_busts_when_default_path_swapped(tmp_path, monkeypatch):
    """Two different resolved paths must never share a cache slot."""
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"docker": True, "models": ["piper"]}), encoding="utf-8")
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"docker": False, "models": ["orpheus"]}), encoding="utf-8")

    monkeypatch.setattr(sp, "DEFAULT_PROFILE_PATH", a)
    first = sp.load_startup_profile()
    assert first.models == ["piper"]

    monkeypatch.setattr(sp, "DEFAULT_PROFILE_PATH", b)
    second = sp.load_startup_profile()
    assert second.models == ["orpheus"], "stale cache hit from path a's slot"


def test_normalize_rejects_non_dict():
    with pytest.raises(ValueError):
        sp.normalize_profile("nope")


def test_normalize_rejects_non_list_models():
    with pytest.raises(ValueError):
        sp.normalize_profile({"models": "piper"})


def test_normalize_filters_unknown_model_ids(monkeypatch):
    monkeypatch.setattr("src.model_registry.launchable_local_ids", lambda host=None: ["piper", "qwen35_4b"])
    clean = sp.normalize_profile({"models": ["piper", "not-a-real-id", "qwen35_4b"]})
    assert clean.models == ["piper", "qwen35_4b"]


def test_save_writes_atomically_and_busts_cache(tmp_path, monkeypatch):
    target = tmp_path / "startup_profile.json"
    monkeypatch.setattr(sp, "DEFAULT_PROFILE_PATH", target)
    # No example template either, so the prime below is the true default.
    monkeypatch.setattr(sp, "EXAMPLE_PROFILE_PATH", tmp_path / "startup_profile.example.json")
    monkeypatch.setattr("src.model_registry.launchable_local_ids", lambda host=None: ["piper"])

    # Prime the cache with the (missing-file) default.
    assert sp.load_startup_profile().models == []

    saved = sp.save_startup_profile({"docker": False, "langfuse": True, "mac_mini_sync": True, "models": ["piper"]})
    assert saved.docker is False
    assert saved.models == ["piper"]

    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["models"] == ["piper"]

    # Cache must reflect the new state, not the primed default.
    reread = sp.load_startup_profile()
    assert reread.models == ["piper"]
