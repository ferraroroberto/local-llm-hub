"""Unit tests for the transcription glossary (issue #90).

Covers the replacement engine (ordering, word-boundary, case-insensitivity,
no over-match) and the response post-processor across the OpenAI
``response_format`` shapes whisper-server emits, plus an end-to-end check
that the hub's audio proxy rewrites a glossary term in the upstream response.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

import pytest  # noqa: E402

from src.transcription_glossary import (  # noqa: E402
    apply_rules,
    apply_to_response,
    load_boost_terms,
    load_rules,
)


# ---------------------------------------------------------------------------
# Replacement engine
# ---------------------------------------------------------------------------

def _rules(pairs):
    """Compile a list of (from, to) tuples into Rule objects via the loader."""
    from src.transcription_glossary import _compile_rules

    return tuple(_compile_rules([{"from": f, "to": t} for f, t in pairs]))


def test_basic_replacement():
    rules = _rules([("cloud code", "Claude Code")])
    assert apply_rules("i opened cloud code today", rules) == "i opened Claude Code today"


def test_case_insensitive_match_canonical_replacement():
    rules = _rules([("cloud code", "Claude Code")])
    assert apply_rules("Cloud Code", rules) == "Claude Code"
    assert apply_rules("CLOUD CODE", rules) == "Claude Code"


def test_word_boundary_no_overmatch():
    rules = _rules([("quen", "Qwen")])
    # 'quench' must not be touched — \b prevents the partial match.
    assert apply_rules("quench the quen", rules) == "quench the Qwen"


def test_non_listed_text_unchanged():
    rules = _rules([("cloud code", "Claude Code")])
    text = "nothing here matches the glossary at all"
    assert apply_rules(text, rules) == text


def test_longest_phrase_first_ordering():
    # A short rule that overlaps a longer one must not pre-empt it: the
    # loader sorts longest-first regardless of input order, so the long
    # phrase claims the span before the short rule can. (File order here
    # is short-then-long, which would mangle it without the sort.)
    rules = _rules([("claw", "CLAW"), ("open claw", "openClaw")])
    assert apply_rules("open claw", rules) == "openClaw"


def test_empty_rules_is_identity():
    assert apply_rules("cloud code", tuple()) == "cloud code"


# ---------------------------------------------------------------------------
# Response post-processor
# ---------------------------------------------------------------------------

def test_apply_to_response_json_text_field():
    rules = _rules([("cloud code", "Claude Code")])
    body = json.dumps({"text": "the cloud code thing"}).encode("utf-8")
    out = apply_to_response(body, "application/json", rules)
    assert json.loads(out)["text"] == "the Claude Code thing"


def test_apply_to_response_verbose_json_segments():
    rules = _rules([("quen", "Qwen")])
    body = json.dumps(
        {
            "text": "i like quen",
            "segments": [
                {"id": 0, "text": "i like quen"},
                {"id": 1, "text": "nothing here"},
            ],
        }
    ).encode("utf-8")
    out = json.loads(apply_to_response(body, "application/json", rules))
    assert out["text"] == "i like Qwen"
    assert out["segments"][0]["text"] == "i like Qwen"
    assert out["segments"][1]["text"] == "nothing here"


def test_apply_to_response_plain_text():
    rules = _rules([("cloud code", "Claude Code")])
    out = apply_to_response(b" cloud code\n", "text/plain", rules)
    assert out == " Claude Code\n".encode("utf-8")


def test_apply_to_response_unknown_content_type_untouched():
    rules = _rules([("cloud code", "Claude Code")])
    body = b"\x00\x01cloud code"
    assert apply_to_response(body, "application/octet-stream", rules) == body


def test_apply_to_response_invalid_json_untouched():
    rules = _rules([("cloud code", "Claude Code")])
    body = b"{not valid json cloud code"
    assert apply_to_response(body, "application/json", rules) == body


def test_apply_to_response_empty_rules_is_byte_identical():
    body = json.dumps({"text": "cloud code"}).encode("utf-8")
    assert apply_to_response(body, "application/json", tuple()) == body


# ---------------------------------------------------------------------------
# Committed config loads + the seed rules are present
# ---------------------------------------------------------------------------

def test_committed_glossary_loads_seed_rules():
    load_rules.cache_clear()
    rules = load_rules()
    assert rules, "committed config/transcription_glossary.json yielded no rules"
    body = json.dumps({"text": "cloud code with open claw and quen"}).encode("utf-8")
    out = json.loads(apply_to_response(body, "application/json", rules))
    assert out["text"] == "Claude Code with openClaw and Qwen"


def test_committed_glossary_has_boost_terms():
    terms = load_boost_terms()
    assert "Claude Code" in terms and "Qwen" in terms


# ---------------------------------------------------------------------------
# End-to-end through the hub audio proxy
# ---------------------------------------------------------------------------

def test_proxy_rewrites_glossary_term_in_response(monkeypatch):
    """POST /v1/audio/transcriptions → the upstream 'cloud code' is fixed."""
    import httpx as _httpx_mod
    from fastapi.testclient import TestClient

    from src import server as server_mod

    class _FakeResp:
        status_code = 200
        content = json.dumps({"text": "open cloud code please"}).encode("utf-8")
        headers = {"content-type": "application/json"}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            return _FakeResp()

    monkeypatch.setattr(_httpx_mod, "AsyncClient", _FakeAsyncClient)
    load_rules.cache_clear()

    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 100)

    client = TestClient(server_mod.app)
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("audio.wav", buf.getvalue(), "audio/wav")},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "open Claude Code please"
