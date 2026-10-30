"""Unit tests for whisper vocabulary-boosting arg injection (issue #91).

`_whisper_boost_args` sources the initial prompt from the committed
dictionary's `boost_terms` when a whisper row opts into
`--carry-initial-prompt`, so the boosting vocabulary lives in one place
(config/transcription_glossary.json, shared with the #90 rules).
"""

from __future__ import annotations

import json
import logging
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

from src.process.backend_process import _whisper_boost_args  # noqa: E402
from src.audio import transcription_glossary as tg  # noqa: E402
from src.audio.transcription_glossary import (  # noqa: E402
    DEFAULT_BOOST_TOKEN_BUDGET,
    build_boost_plan,
    estimate_tokens,
    load_boost_terms,
)


def test_no_boost_without_carry_flag():
    # The #88 translate row (maxctx 0, no carry) must get nothing injected.
    assert _whisper_boost_args(["--max-context", "0", "--suppress-nst"]) == []


def test_boost_args_sourced_from_dictionary(monkeypatch, tmp_path):
    committed = tmp_path / "glossary.json"
    committed.write_text(json.dumps({"boost_terms": ["Claude Code", "Qwen"]}))
    monkeypatch.setattr(tg, "DEFAULT_GLOSSARY_PATH", committed)
    monkeypatch.setattr(tg, "DEFAULT_LOCAL_BOOST_PATH", tmp_path / "none.json")
    out = _whisper_boost_args(["--max-context", "64", "--carry-initial-prompt"])
    assert out == ["--prompt", "Glossary: Claude Code, Qwen."]


def test_explicit_prompt_is_not_overridden():
    # If a row already supplies its own --prompt, don't second-guess it.
    args = ["--carry-initial-prompt", "--prompt", "my own prompt"]
    assert _whisper_boost_args(args) == []


def test_boost_terms_merges_local_overlay(tmp_path):
    # issue #290: a gitignored local overlay merges in behind the committed
    # dictionary, so private vocabulary never has to be committed.
    committed = tmp_path / "glossary.json"
    committed.write_text(json.dumps({"boost_terms": ["Claude Code"]}))
    local = tmp_path / "glossary.local.json"
    local.write_text(json.dumps({"boost_terms": ["Roberto"]}))

    assert load_boost_terms(str(committed), str(local)) == ["Claude Code", "Roberto"]


def test_boost_terms_local_overlay_missing_is_noop(tmp_path):
    committed = tmp_path / "glossary.json"
    committed.write_text(json.dumps({"boost_terms": ["Claude Code"]}))
    missing_local = tmp_path / "does-not-exist.json"

    assert load_boost_terms(str(committed), str(missing_local)) == ["Claude Code"]


# Token-budget cap: whisper honours only the tail of a long initial prompt.


def _plan(tmp_path, terms, aliases=None, budget=None, extra=None):
    committed = tmp_path / "glossary.json"
    body = {"boost_terms": terms, **(extra or {})}
    committed.write_text(json.dumps(body))
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({"aliases": aliases or []}))
    return build_boost_plan(
        str(committed), str(tmp_path / "none.json"), str(alias_file), budget
    )


def test_under_budget_prompt_is_unchanged(tmp_path):
    plan = _plan(tmp_path, ["Claude Code", "Qwen"])
    assert plan.prompt == "Glossary: Claude Code, Qwen."
    assert plan.dropped == []
    assert plan.budget == DEFAULT_BOOST_TOKEN_BUDGET


def test_over_budget_prompt_stays_within_budget_and_keeps_top_terms(tmp_path):
    terms = [f"Filler{i:03d}" for i in range(100)]
    plan = _plan(tmp_path, terms)
    assert plan.dropped  # 100 terms cannot fit 200 tokens
    assert plan.used_tokens <= plan.budget
    assert estimate_tokens(plan.prompt) <= plan.budget
    # No alias signal → file order is the ranking: the head survives.
    assert plan.kept == terms[: len(plan.kept)]
    assert plan.dropped == terms[len(plan.kept):]
    assert plan.total_tokens > plan.budget


def test_alias_with_more_mishearings_outranks_file_order(tmp_path):
    terms = [f"Filler{i:03d}" for i in range(100)] + ["Zorbo"]
    aliases = [{"canonical": "Zorbo", "variants": ["zorbo", "sorbo"]}]
    plan = _plan(tmp_path, terms, aliases)
    assert "Zorbo" in plan.kept
    assert plan.kept[-1] == "Zorbo"  # survivors keep file order
    assert plan.used_tokens <= plan.budget


def test_ranking_is_deterministic(tmp_path):
    terms = [f"Filler{i:03d}" for i in range(100)] + ["Zorbo"]
    aliases = [{"canonical": "Zorbo", "variants": ["sorbo"]}]
    assert _plan(tmp_path, terms, aliases) == _plan(tmp_path, terms, aliases)


def test_budget_comes_from_glossary_file(tmp_path):
    plan = _plan(tmp_path, ["Alpha", "Bravo", "Charlie"], extra={"boost_token_budget": 8})
    assert plan.budget == 8
    assert plan.dropped  # three terms cannot fit 8 tokens
    assert plan.used_tokens <= 8


def test_invalid_budget_falls_back_to_default(tmp_path):
    assert _plan(tmp_path, ["A"], extra={"boost_token_budget": -5}).budget == DEFAULT_BOOST_TOKEN_BUDGET
    assert _plan(tmp_path, ["A"], extra={"boost_token_budget": "big"}).budget == DEFAULT_BOOST_TOKEN_BUDGET
    assert _plan(tmp_path, ["A"], extra={"boost_token_budget": True}).budget == DEFAULT_BOOST_TOKEN_BUDGET


def test_term_longer_than_budget_yields_no_prompt(tmp_path):
    plan = _plan(tmp_path, ["x" * 700])
    assert plan.prompt == ""
    assert plan.dropped == ["x" * 700]


def test_launch_logs_dropped_terms_at_info(monkeypatch, tmp_path, caplog):
    committed = tmp_path / "glossary.json"
    terms = [f"Filler{i:03d}" for i in range(100)]
    committed.write_text(json.dumps({"boost_terms": terms}))
    monkeypatch.setattr(tg, "DEFAULT_GLOSSARY_PATH", committed)
    monkeypatch.setattr(tg, "DEFAULT_LOCAL_BOOST_PATH", tmp_path / "none.json")
    with caplog.at_level(logging.INFO):
        out = _whisper_boost_args(["--carry-initial-prompt"])
    assert out[0] == "--prompt"
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("over budget" in m and "Filler099" in m for m in messages)
