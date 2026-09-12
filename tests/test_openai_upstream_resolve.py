"""The shared ``openai``-backend dispatch helpers (#556).

``resolve_openai_upstream`` is the one home of the remote-vs-local rule: all
four dispatch paths (``/v1/messages`` and ``/v1/chat/completions``, buffered
and streaming) resolve their upstream through it. ``call_openai_upstream`` is
the one home of the *buffered* call itself — the on-demand tracking context
and the ``UpstreamError`` -> 502 mapping that both buffered routes share.
These cases pin both rules once instead of per route.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src import chat_translation
from src.model_registry import Model
from src.openai_upstream import UpstreamError


def _model(**kw) -> Model:
    base = dict(id="qwen35_4b", display_name="qwen3.5-4b", backend="openai", port=8088)
    base.update(kw)
    return Model(**base)


def test_local_model_targets_its_own_url_by_display_name(monkeypatch):
    monkeypatch.setattr(chat_translation, "remote_base_url", lambda m: None)
    model = _model()

    upstream = chat_translation.resolve_openai_upstream(model)

    assert upstream.remote is None
    assert upstream.base_url == model.url
    assert upstream.model_name == "qwen3.5-4b"
    assert upstream.headers is None


def test_remote_model_targets_peer_hub_by_id_with_its_token(monkeypatch):
    monkeypatch.setattr(chat_translation, "remote_base_url", lambda m: "http://peer:8000")
    monkeypatch.setattr(chat_translation, "remote_auth_token_for_model", lambda m: "tok")

    upstream = chat_translation.resolve_openai_upstream(_model())

    assert upstream.remote == "http://peer:8000"
    assert upstream.base_url == "http://peer:8000/v1"
    assert upstream.model_name == "qwen35_4b"
    assert upstream.headers == {"Authorization": "Bearer tok"}


def test_model_without_url_is_a_500(monkeypatch):
    monkeypatch.setattr(chat_translation, "remote_base_url", lambda m: None)

    with pytest.raises(HTTPException) as exc:
        chat_translation.resolve_openai_upstream(_model(port=None))

    assert exc.value.status_code == 500
    assert "qwen35_4b" in exc.value.detail


def _upstream(**kw) -> chat_translation.OpenAIUpstream:
    base = dict(remote=None, base_url="http://127.0.0.1:8088/v1",
                model_name="qwen3.5-4b", headers=None)
    base.update(kw)
    return chat_translation.OpenAIUpstream(**base)


def test_call_forwards_every_resolved_upstream_field(monkeypatch):
    captured: dict = {}

    def fake_call(base_url, **kw):
        captured["base_url"] = base_url
        captured.update(kw)
        return {"ok": True}

    monkeypatch.setattr(chat_translation, "call_openai_chat", fake_call)

    raw = chat_translation.call_openai_upstream(
        _model(),
        _upstream(remote="http://peer:8000", headers={"Authorization": "Bearer tok"}),
        [{"role": "user", "content": "hi"}],
        max_tokens=7,
        temperature=0.25,
        extra={"chat_template_kwargs": {"enable_thinking": False}},
    )

    assert raw == {"ok": True}
    assert captured["base_url"] == "http://127.0.0.1:8088/v1"
    assert captured["model"] == "qwen3.5-4b"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["max_tokens"] == 7
    assert captured["temperature"] == 0.25
    assert captured["extra"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert captured["headers"] == {"Authorization": "Bearer tok"}


def test_call_normalises_an_empty_extra_to_none(monkeypatch):
    """``extra={}`` must reach the upstream as ``None``, not an empty dict —
    the callers pass their overlay through unconditionally."""
    captured: dict = {}
    monkeypatch.setattr(
        chat_translation, "call_openai_chat",
        lambda base_url, **kw: captured.update(kw) or {},
    )

    chat_translation.call_openai_upstream(_model(), _upstream(), [], extra={})

    assert captured["extra"] is None


def test_unreachable_upstream_becomes_a_502(monkeypatch):
    """Both buffered routes rely on this mapping; the streaming ones
    deliberately emit an in-stream SSE error instead."""
    def boom(*a, **kw):
        raise UpstreamError("upstream http://127.0.0.1:9/v1 unreachable: refused")

    monkeypatch.setattr(chat_translation, "call_openai_chat", boom)

    with pytest.raises(HTTPException) as exc:
        chat_translation.call_openai_upstream(_model(), _upstream(), [])

    assert exc.value.status_code == 502
    assert "unreachable" in exc.value.detail
