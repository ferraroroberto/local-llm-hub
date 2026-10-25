"""Tests for ``<think>`` stripping and the SSE filter pipeline.

Covers:

- ``ThinkStripper`` over chunked input (tag straddles boundary).
- ``strip_think_blocks`` on a complete string.
- ``clean_openai_response`` (non-stream): strips think tags and folds
  ``reasoning_content`` into empty ``content``.
- ``iter_cleaned_sse``: full SSE filter pipeline yields cleaned
  ``data:`` frames and passes through ``[DONE]`` / blank lines.
"""

from __future__ import annotations

import json
from typing import List

from src.openai_upstream import (
    ThinkStripper,
    clean_openai_response,
    iter_cleaned_sse,
    strip_think_blocks,
)


# ---- ThinkStripper unit tests ----

def test_strip_think_blocks_complete_string():
    src = "Before <think>secret</think>after"
    assert strip_think_blocks(src) == "Before after"


def test_strip_think_blocks_multiline():
    src = "x<think>\nlots\nof\nthink\n</think>y"
    assert strip_think_blocks(src) == "xy"


def test_strip_think_blocks_unterminated_drops_tail():
    # Truncated mid-thought (e.g. max_tokens cut it off): no close tag
    # to match, so the naive regex would leave the raw block in place.
    src = "answer is...<think>still thinking when stream died"
    assert strip_think_blocks(src) == "answer is..."


def test_strip_think_blocks_unterminated_only_think():
    src = "<think>never got to an answer"
    assert strip_think_blocks(src) == ""


def test_strip_think_blocks_well_formed_then_unterminated():
    src = "<think>done</think>Final answer<think>more thinking cut off"
    assert strip_think_blocks(src) == "Final answer"


def test_think_stripper_split_open_tag():
    s = ThinkStripper()
    out1 = s.feed("hello <thi")
    out2 = s.feed("nk>secret</think>world")
    out3 = s.flush()
    assert out1 + out2 + out3 == "hello world"


def test_think_stripper_split_close_tag():
    s = ThinkStripper()
    out1 = s.feed("a<think>thinking ab")
    out2 = s.feed("out it</thi")
    out3 = s.feed("nk>b")
    out4 = s.flush()
    assert out1 + out2 + out3 + out4 == "ab"


def test_think_stripper_no_tags_pass_through():
    s = ThinkStripper()
    parts = ["he", "ll", "o ", "wor", "ld"]
    out = "".join(s.feed(p) for p in parts) + s.flush()
    assert out == "hello world"


def test_think_stripper_unterminated_drops_tail():
    # Stream cut off mid-thinking: nothing to recover.
    s = ThinkStripper()
    out = s.feed("answer is...<think>still thinking when stream died")
    assert out == "answer is..."
    assert s.flush() == ""


# ---- clean_openai_response (non-stream) ----

def _resp(content: str = "", reasoning_content: str = "") -> dict:
    msg = {"role": "assistant", "content": content}
    if reasoning_content:
        msg["reasoning_content"] = reasoning_content
    return {
        "id": "x", "object": "chat.completion", "model": "qwen3.5-4b",
        "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_clean_openai_response_strips_think():
    r = _resp(content="<think>plan</think>The answer is 4.")
    clean_openai_response(r)
    assert r["choices"][0]["message"]["content"] == "The answer is 4."


def test_clean_openai_response_folds_reasoning_when_content_empty():
    r = _resp(content="", reasoning_content="The answer is 4.")
    clean_openai_response(r)
    assert r["choices"][0]["message"]["content"] == "The answer is 4."


def test_clean_openai_response_prefers_content_when_present():
    r = _resp(content="Direct answer", reasoning_content="Long reasoning")
    clean_openai_response(r)
    # Don't clobber a real content field with reasoning.
    assert r["choices"][0]["message"]["content"] == "Direct answer"


def test_clean_openai_response_drops_unterminated_think_on_truncation():
    r = _resp(content="<think>never finished reasoning")
    r["choices"][0]["finish_reason"] = "length"
    clean_openai_response(r)
    # A truncated response should degrade to empty content, not leak
    # raw <think> internals to the caller.
    assert r["choices"][0]["message"]["content"] == ""
    assert r["choices"][0]["finish_reason"] == "length"


# ---- iter_cleaned_sse pipeline ----

def _sse_lines(*chunks: dict) -> List[str]:
    lines: List[str] = []
    for c in chunks:
        lines.append("data: " + json.dumps(c))
        lines.append("")  # SSE record terminator
    lines.append("data: [DONE]")
    return lines


def _delta(content: str = "", reasoning_content: str = "") -> dict:
    delta = {}
    if content:
        delta["content"] = content
    if reasoning_content:
        delta["reasoning_content"] = reasoning_content
    return {
        "id": "x", "object": "chat.completion.chunk", "model": "qwen3.5-4b",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


def test_iter_cleaned_sse_strips_think_across_chunks():
    raw = _sse_lines(
        _delta("Hello <thi"),
        _delta("nk>secret</think>world"),
        _delta(" friend"),
    )
    cleaned: List[str] = list(iter_cleaned_sse(iter(raw)))
    # Reassemble the content deltas the client would see.
    seen_content = ""
    for line in cleaned:
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            continue
        obj = json.loads(payload)
        delta = obj["choices"][0].get("delta", {})
        seen_content += delta.get("content") or ""
    assert seen_content == "Hello world friend"


def test_iter_cleaned_sse_passes_done_unchanged():
    raw = ["data: [DONE]"]
    out = list(iter_cleaned_sse(iter(raw)))
    assert out == ["data: [DONE]"]


def test_iter_cleaned_sse_passes_blank_lines():
    raw = ["", "data: [DONE]"]
    out = list(iter_cleaned_sse(iter(raw)))
    assert out == ["", "data: [DONE]"]


def test_iter_cleaned_sse_flushes_trailing_tag_lookalike_before_done():
    """#529: a stream whose last content delta ends near a ``<`` leaves
    ``ThinkStripper`` holding back that tail as a possible split open-tag.
    Without a flush before ``[DONE]``, that tail is silently dropped."""
    raw = _sse_lines(
        _delta("Answer: done."),
        _delta("</p>"),
    )
    cleaned: List[str] = list(iter_cleaned_sse(iter(raw)))
    assert cleaned[-1] == "data: [DONE]"

    seen_content = ""
    for line in cleaned:
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            continue
        obj = json.loads(payload)
        delta = obj["choices"][0].get("delta", {})
        seen_content += delta.get("content") or ""
    assert seen_content == "Answer: done.</p>"
