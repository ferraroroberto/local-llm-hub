"""``resolve_openai_upstream`` — the one home of the remote-vs-local rule for
``openai``-backend chat dispatch (#556).

All four dispatch paths (``/v1/messages`` and ``/v1/chat/completions``,
buffered and streaming) resolve their upstream through it, so these cases pin
the rule once instead of per route.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src import chat_translation
from src.model_registry import Model


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
