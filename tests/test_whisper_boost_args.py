"""Unit tests for whisper vocabulary-boosting arg injection (issue #91).

`_whisper_boost_args` sources the initial prompt from the committed
dictionary's `boost_terms` when a whisper row opts into
`--carry-initial-prompt`, so the boosting vocabulary lives in one place
(config/transcription_glossary.json, shared with the #90 rules).
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

from src.backend_process import _whisper_boost_args  # noqa: E402
from src.transcription_glossary import load_boost_terms  # noqa: E402


def test_no_boost_without_carry_flag():
    # The #88 translate row (maxctx 0, no carry) must get nothing injected.
    assert _whisper_boost_args(["--max-context", "0", "--suppress-nst"]) == []


def test_boost_args_sourced_from_dictionary():
    out = _whisper_boost_args(["--max-context", "64", "--carry-initial-prompt"])
    assert out[0] == "--prompt"
    prompt = out[1]
    # Every committed boost term must appear in the launch prompt.
    for term in load_boost_terms():
        assert term in prompt


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
