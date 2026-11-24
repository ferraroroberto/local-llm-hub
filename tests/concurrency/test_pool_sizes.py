"""Worker pool sizes: defaults with nothing saved, a saved size used by the next start, bad values refused."""

from __future__ import annotations

import importlib
import json

import pytest

from src.concurrency import pool_sizes, pools

TODAYS_SIZES = {"hot-poll": 8, "heavy-read": 4, "action": 12, "fan-out": 8, "background": 6, "media": 8}


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "worker_pools.json"
    monkeypatch.setattr(pool_sizes, "DEFAULT_PATH", path)
    return path


@pytest.fixture
def rebuilt_pools(config_path):
    """``pools`` re-imported against the fake config, then restored: a start of the hub in miniature."""
    yield lambda: importlib.reload(pools)
    pool_sizes.DEFAULT_PATH = config_path.parent / "absent.json"
    importlib.reload(pools)


def test_no_saved_file_gives_todays_sizes(config_path, rebuilt_pools):
    assert not config_path.exists()
    assert pool_sizes.DEFAULTS == TODAYS_SIZES
    assert pool_sizes.effective_sizes() == TODAYS_SIZES
    built = rebuilt_pools()
    assert built.RUNNING_SIZES == TODAYS_SIZES
    assert built.ACTION._max_workers == 12
    assert built.HEAVY_READ._max_workers == 4


def test_a_saved_size_is_used_after_restart(config_path, rebuilt_pools):
    pool_sizes.save({"action": 20, "media": 2})
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"action": 20, "media": 2}

    built = rebuilt_pools()

    assert built.ACTION._max_workers == 20
    assert built.MEDIA._max_workers == 2
    assert built.HOT_POLL._max_workers == 8


@pytest.mark.parametrize("value", [0, -3, 65, 1.5, "8", True, None])
def test_out_of_range_or_non_integer_is_refused_with_a_message(config_path, value):
    with pytest.raises(ValueError, match=r"hot-poll: size must be .*1 to 64"):
        pool_sizes.save({"hot-poll": value})
    assert not config_path.exists()


def test_unknown_pool_is_refused(config_path):
    with pytest.raises(ValueError, match="unknown pool 'nope'"):
        pool_sizes.save({"nope": 3})


def test_a_refused_save_keeps_what_was_saved(config_path):
    pool_sizes.save({"action": 20})
    with pytest.raises(ValueError):
        pool_sizes.save({"media": 3, "fan-out": 0})
    assert pool_sizes.load_saved() == {"action": 20}


def test_a_broken_file_never_stops_the_hub(config_path):
    config_path.write_text("{not json", encoding="utf-8")
    assert pool_sizes.effective_sizes() == TODAYS_SIZES
    config_path.write_text("[1, 2]", encoding="utf-8")
    assert pool_sizes.effective_sizes() == TODAYS_SIZES


def test_one_bad_entry_falls_back_alone(config_path):
    config_path.write_text(json.dumps({"action": 99, "media": 3, "ghost": 2}), encoding="utf-8")
    assert pool_sizes.effective_sizes() == {**TODAYS_SIZES, "media": 3}
