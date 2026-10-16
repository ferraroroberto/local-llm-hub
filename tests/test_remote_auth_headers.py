"""``remote_proxy.remote_auth_headers`` — the one builder of the peer-hub
``Authorization`` header (#789). Replaces six private copies that disagreed on
their empty value; the shared helper always returns a dict."""

from __future__ import annotations

from src import remote_proxy
from src.model_registry import Model

_TOKEN = "dummy-test-token"
_ENV = "LOCAL_LLM_HUB_TOKEN_PEER_ONE"


def _model() -> Model:
    return Model(id="m1", display_name="m1", backend="openai", port=8088)


def test_host_id_with_token_gets_a_bearer_header(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)

    assert remote_proxy.remote_auth_headers("peer-one") == {"Authorization": f"Bearer {_TOKEN}"}


def test_host_id_without_token_gets_an_empty_dict(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)

    assert remote_proxy.remote_auth_headers("peer-one") == {}


def test_blank_token_counts_as_absent(monkeypatch):
    monkeypatch.setenv(_ENV, "")

    assert remote_proxy.remote_auth_headers("peer-one") == {}


def test_model_with_token_gets_a_bearer_header(monkeypatch):
    monkeypatch.setattr(remote_proxy, "remote_auth_token_for_model", lambda m: _TOKEN)

    assert remote_proxy.remote_auth_headers(_model()) == {"Authorization": f"Bearer {_TOKEN}"}


def test_model_without_token_gets_an_empty_dict(monkeypatch):
    monkeypatch.setattr(remote_proxy, "remote_auth_token_for_model", lambda m: None)

    assert remote_proxy.remote_auth_headers(_model()) == {}
