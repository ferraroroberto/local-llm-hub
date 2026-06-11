"""Unit tests for whisper vocabulary-boosting arg injection (issue #91).

`_whisper_boost_args` sources the initial prompt from the committed
dictionary's `boost_terms` when a whisper row opts into
`--carry-initial-prompt`, so the boosting vocabulary lives in one place
(config/transcription_glossary.json, shared with the #90 rules).
"""

from __future__ import annotations

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
