"""Scoring helpers of scripts/canary_preflight.py: the numbers in
docs/canary-translate-preflight.md come from these, so pin their behaviour."""

from __future__ import annotations

import pytest

from scripts import canary_preflight as pf


def test_identical_text_scores_100():
    refs = ["The cabbage juice changes color depending on acidity.", "Madagascar is by far the biggest."]
    assert pf.corpus_bleu(refs, refs) == pytest.approx(100.0)


def test_no_overlap_scores_zero():
    assert pf.corpus_bleu(["alpha beta gamma delta epsilon"], ["one two three four five"]) == 0.0


def test_spanish_answer_scores_far_below_a_translation():
    ref = "Parisians have a reputation for being egocentric, rude and arrogant."
    translated = "Parisians have a reputation for being egocentric, rude, and conceited."
    spanish = "Los parisinos tienen reputación de egocéntricos, descorteses y engreídos."
    assert pf.corpus_bleu([translated], [ref]) > 40
    assert pf.corpus_bleu([spanish], [ref]) == 0.0


def test_short_hypothesis_is_penalised():
    ref = "The relationship between brain pathology and behaviour supports scientists in their research."
    assert pf.corpus_bleu(["The relationship between brain pathology"], [ref]) < pf.corpus_bleu([ref], [ref])


def test_tokenizer_splits_punctuation_but_keeps_numbers():
    assert pf._tokenize("Hello, world.") == ["Hello", ",", "world", "."]
    assert "800,000" in pf._tokenize("over 800,000 soldiers")


def test_unigram_f1_ignores_case_and_punctuation():
    assert pf.unigram_f1("The Cat, sat.", "the cat sat") == pytest.approx(1.0)
    assert pf.unigram_f1("dog", "cat") == 0.0
