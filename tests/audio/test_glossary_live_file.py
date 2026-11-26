"""The live dictionary is local state: a save must never dirty a tracked file."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.audio import transcription_glossary as tg
from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE = "config/transcription_glossary.json"
SAMPLE = "config/transcription_glossary.sample.json"


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW,
    )
    return done.stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clean clone-like checkout: this repo's .gitignore and sample, no live file."""
    root = tmp_path / "checkout"
    (root / "config").mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / ".gitignore", root / ".gitignore")
    (root / SAMPLE).write_text(
        json.dumps({
            "replacements": [{"from": "quen", "to": "Qwen"}],
            "boost_terms": ["Qwen"],
            "boost_token_budget": 120,
        }),
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q", str(root))
    _git(root, "add", ".gitignore", SAMPLE)
    _git(
        root, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "-c", "core.hooksPath=no-hooks", "commit", "-qm", "seed",
    )
    monkeypatch.setattr(tg, "DEFAULT_GLOSSARY_PATH", root / LIVE)
    monkeypatch.setattr(tg, "SAMPLE_GLOSSARY_PATH", root / SAMPLE)
    tg.load_rules.cache_clear()
    return root


def test_sample_answers_until_the_first_save(checkout: Path) -> None:
    assert tg.load_glossary()["boost_terms"] == ["Qwen"]
    assert [r.replacement for r in tg.load_rules()] == ["Qwen"]
    assert tg.load_boost_budget() == 120
    assert not (checkout / LIVE).exists()


def test_save_writes_the_live_file_and_leaves_the_tree_clean(checkout: Path) -> None:
    sample_before = (checkout / SAMPLE).read_bytes()

    tg.save_glossary({"replacements": [], "boost_terms": ["Codex"]})

    assert _git(checkout, "status", "--porcelain") == ""
    assert (checkout / SAMPLE).read_bytes() == sample_before
    assert tg.load_glossary() == {"replacements": [], "boost_terms": ["Codex"]}
    assert tg.load_rules() == ()
    # The editor never sends the budget: the first save carries the sample's.
    assert tg.load_boost_budget() == 120


def test_this_repo_ignores_and_does_not_track_the_live_file() -> None:
    assert _git(REPO_ROOT, "ls-files", "--", LIVE) == ""
    assert _git(REPO_ROOT, "check-ignore", "--no-index", "--", LIVE) == LIVE
    assert _git(REPO_ROOT, "ls-files", "--", SAMPLE) == SAMPLE
