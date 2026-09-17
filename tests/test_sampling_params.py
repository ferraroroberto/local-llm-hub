"""Tests for stop/sampling/thinking parameters on ``POST /v1/messages`` (#607).

Before #607 ``MessagesRequest`` declared none of ``stop_sequences`` / ``top_p``
/ ``top_k`` / ``thinking``, so Pydantic dropped them silently. Now:

- local ``openai`` backends forward ``stop_sequences`` (as ``stop``), ``top_p``
  and ``top_k`` upstream, buffered and streaming;
- the CLI backends refuse ``stop_sequences`` with a 400 and accept ``top_p`` /
  ``top_k`` as no-ops (like ``temperature``);
- ``thinking`` other than ``{"type": "disabled"}`` is a 400 on every backend.
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest
from fastapi.testclient import TestClient

from src import server as server_mod
from tests.test_tool_use import LOCAL_MODEL, _messages_upstream_extra


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
def test_sampling_params_reach_the_openai_backend(monkeypatch, stream):
    extra = _messages_upstream_extra(monkeypatch, {
        "model": LOCAL_MODEL,
        "stream": stream,
        "stop_sequences": ["\n\nHuman:", "END"],
        "top_p": 0.9,
        "top_k": 40,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert extra["stop"] == ["\n\nHuman:", "END"]
    assert extra["top_p"] == 0.9
    assert extra["top_k"] == 40


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
def test_request_without_sampling_params_sends_none(monkeypatch, stream):
    extra = _messages_upstream_extra(monkeypatch, {
        "model": LOCAL_MODEL,
        "stream": stream,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert not {"stop", "top_p", "top_k"} & set(extra or {})


@pytest.mark.parametrize("model", ["claude_haiku", "gemini_pro"])
@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
def test_stop_sequences_on_a_cli_backend_are_refused(model, stream):
    client = TestClient(server_mod.app)
    response = client.post("/v1/messages", json={
        "model": model,
        "stream": stream,
        "stop_sequences": ["END"],
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert "'stop_sequences'" in body["error"]["message"]


def test_top_p_top_k_on_a_cli_backend_are_accepted(monkeypatch):
    monkeypatch.setattr(
        server_mod, "_run_claude_backend",
        lambda model, req: {"result": "hi", "stop_reason": "end_turn", "usage": {}},
    )
    client = TestClient(server_mod.app)
    response = client.post("/v1/messages", json={
        "model": "claude_haiku",
        "top_p": 0.5,
        "top_k": 10,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert response.status_code == 200
    assert response.json()["content"] == [{"type": "text", "text": "hi"}]


@pytest.mark.parametrize("model", ["claude_haiku", "gemini_pro", LOCAL_MODEL])
@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
def test_enabled_thinking_is_refused_on_every_backend(model, stream):
    client = TestClient(server_mod.app)
    response = client.post("/v1/messages", json={
        "model": model,
        "stream": stream,
        "max_tokens": 2048,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert response.status_code == 400
    assert "extended thinking" in response.json()["error"]["message"]


@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streaming"])
def test_disabled_thinking_is_accepted(monkeypatch, stream):
    extra = _messages_upstream_extra(monkeypatch, {
        "model": LOCAL_MODEL,
        "stream": stream,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert "thinking" not in (extra or {})
