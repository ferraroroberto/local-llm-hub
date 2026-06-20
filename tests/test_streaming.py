"""Tests for SSE streaming and ``<think>`` stripping in the OpenAI shape.

Covers:

- ``ThinkStripper`` over chunked input (tag straddles boundary).
- ``strip_think_blocks`` on a complete string.
- ``clean_openai_response`` (non-stream): strips think tags and folds
  ``reasoning_content`` into empty ``content``.
- ``iter_cleaned_sse``: full SSE filter pipeline yields cleaned
  ``data:`` frames and passes through ``[DONE]`` / blank lines.
- ``/v1/chat/completions`` with ``stream=true``: returns SSE
  (``text/event-stream``) and proxies cleaned chunks.
- ``/v1/chat/completions`` non-stream: response has think blocks
  removed.
"""

from __future__ import annotations

import json
import os
from typing import Iterator, List

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

from fastapi.testclient import TestClient

from src import openai_upstream as upstream_mod
from src import server as server_mod
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


# ---- end-to-end against /v1/chat/completions ----

def test_chat_completions_strips_think_non_stream(monkeypatch):
    def fake_call(base_url, model, messages, *, max_tokens=None, temperature=None,
                  timeout=600.0, extra=None):
        return {
            "id": "x", "object": "chat.completion", "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "<think>step 1\nstep 2</think>final answer",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
        }

    monkeypatch.setattr(server_mod, "call_openai_chat", fake_call)

    client = TestClient(server_mod.app)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3.5-4b",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "final answer"


def test_chat_completions_streaming_proxies_sse(monkeypatch):
    """End-to-end: stream=true returns SSE with cleaned content deltas."""

    def fake_stream(base_url, model, messages, *, max_tokens=None, temperature=None,
                    timeout=600.0, extra=None) -> Iterator[str]:
        for line in _sse_lines(
            _delta("<think>plan"),
            _delta(" some</think>"),
            _delta("Hello "),
            _delta("world!"),
        ):
            yield line

    monkeypatch.setattr(server_mod, "call_openai_chat_stream", fake_stream)

    client = TestClient(server_mod.app)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen3.5-4b",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())

    # Parse out the data frames.
    reassembled = ""
    saw_done = False
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            saw_done = True
            continue
        obj = json.loads(payload)
        delta = obj["choices"][0].get("delta", {})
        reassembled += delta.get("content") or ""
    assert reassembled == "Hello world!"
    assert saw_done


def test_chat_completions_streaming_usage_populated_from_trailing_frame(monkeypatch):
    """usage_in/usage_out must be captured from the trailing usage frame.

    llama-server (--jinja mode) emits the usage object on a final chunk that
    arrives *after* all content deltas, i.e. after first_token_ns is set.
    This test verifies the fix: usage must be non-zero even when the usage
    frame is not the first data frame.
    """

    def fake_stream(base_url, model, messages, *, max_tokens=None, temperature=None,
                    timeout=600.0, extra=None) -> Iterator[str]:
        # Two content deltas first (these set first_token_ns), then a trailing
        # usage-only chunk (no choices/delta, just a usage field).
        content_chunks = [_delta("Hello "), _delta("world!")]
        usage_chunk = {
            "id": "x", "object": "chat.completion.chunk", "model": model,
            "choices": [],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
        }
        for line in _sse_lines(*content_chunks, usage_chunk):
            yield line

    monkeypatch.setattr(server_mod, "call_openai_chat_stream", fake_stream)

    # Patch record_genai_metrics to capture what usage values were recorded.
    recorded: dict = {}

    def fake_record(*, model, backend, route, client_id, duration_ms,
                    input_tokens=0, output_tokens=0, error_type=""):
        recorded["input_tokens"] = input_tokens
        recorded["output_tokens"] = output_tokens

    monkeypatch.setattr(server_mod, "record_genai_metrics", fake_record)

    client = TestClient(server_mod.app)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen3.5-4b",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as r:
        assert r.status_code == 200
        _ = "".join(r.iter_text())

    assert recorded.get("input_tokens") == 12, f"input_tokens should be 12, got {recorded.get('input_tokens')}"
    assert recorded.get("output_tokens") == 7, f"output_tokens should be 7, got {recorded.get('output_tokens')}"


def test_chat_completions_stream_upstream_error(monkeypatch):
    def fake_stream(*args, **kwargs):
        raise upstream_mod.UpstreamError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(server_mod, "call_openai_chat_stream", fake_stream)

    client = TestClient(server_mod.app)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen3.5-4b",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as r:
        assert r.status_code == 200  # SSE always opens 200
        body = "".join(r.iter_text())
    assert "boom" in body
    assert "[DONE]" in body
