"""Audio role failover (#348).

The audio proxy resolves the ``roles.audio.<role>`` chain (primary + fallback)
and, when a candidate's backend is *unavailable* (connection error / 502-503-504),
transparently retries the next model instead of erroring. An explicit concrete
``model=`` is honoured single-shot (no failover, preserving #128).

The whisper worker binaries are platform/GPU-specific, so these fake the httpx
client and the config rather than driving a real backend.
"""

from __future__ import annotations

import asyncio
import types

import httpx
import pytest
import yaml
from fastapi import HTTPException

from src import host_profile, model_registry, server_audio, transcription_glossary


# --------------------------------------------------------------------------- #
# config helpers (mirror tests/test_model_registry.py)
# --------------------------------------------------------------------------- #
def _write_config(tmp_path, content: dict):
    cfg = tmp_path / "models.yaml"
    cfg.write_text(yaml.safe_dump(content), encoding="utf-8")
    return cfg


def _patch_config_path(monkeypatch, cfg_path):
    monkeypatch.setattr(host_profile, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(model_registry, "CONFIG_PATH", cfg_path, raising=False)
    host_profile._CONFIG_CACHE.clear()


def _two_whisper_config(tmp_path, monkeypatch, *, transcribe: dict):
    cfg = _write_config(tmp_path, {
        "hub": {"port": 8000},
        "hosts": {
            "pc": {"platform": "win32", "default": True, "enabled": ["wa", "wb"]},
        },
        "models": {
            "wa": {"display_name": "whisper-a", "backend": "whisper",
                   "engine": "whisper-server", "port": 9001},
            "wb": {"display_name": "whisper-b", "backend": "whisper",
                   "engine": "whisper-server", "port": 9002},
        },
        "roles": {"audio": {"transcribe": transcribe}},
    })
    _patch_config_path(monkeypatch, cfg)
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc")
    # keep glossary post-processing a no-op so assertions see the raw body
    monkeypatch.setattr(transcription_glossary, "load_rules", lambda: [])


# --------------------------------------------------------------------------- #
# fakes for the proxy round-trip
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {"content-type": "application/json"}


class _FakeClient:
    def __init__(self, handler):
        self._h = handler

    async def post(self, url, **kwargs):
        return self._h(url, kwargs)  # handler returns _FakeResp or raises


class _FakeReq:
    def __init__(self, body=b"----x\r\n", headers=None):
        self._body = body
        self.headers = headers or {"content-type": "multipart/form-data; boundary=x"}
        self.state = types.SimpleNamespace()

    async def body(self):
        return self._body


def _proxy(req):
    return asyncio.run(server_audio._proxy_audio(
        req, default_role="audio_transcribe", ctx_path="/v1/audio/transcriptions"))


# --------------------------------------------------------------------------- #
# audio_role_chain — config parsing
# --------------------------------------------------------------------------- #
def test_role_chain_primary_plus_fallback(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wb"]})
    assert model_registry.audio_role_chain("transcribe") == ["wa", "wb"]


def test_role_chain_single_model(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa"})
    assert model_registry.audio_role_chain("transcribe") == ["wa"]


def test_role_chain_dedups_repeated_primary(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wa", "wb"]})
    assert model_registry.audio_role_chain("transcribe") == ["wa", "wb"]


def test_role_chain_absent_role_is_empty(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa"})
    assert model_registry.audio_role_chain("speech") == []


# --------------------------------------------------------------------------- #
# _whisper_chain_for_request — resolution
# --------------------------------------------------------------------------- #
def test_chain_role_default_resolves_config(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wb"]})
    chain = server_audio._whisper_chain_for_request("", default_role="audio_transcribe")
    assert [m.id for m in chain] == ["wa", "wb"]


def test_chain_explicit_concrete_model_is_single(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wb"]})
    chain = server_audio._whisper_chain_for_request("wb", default_role="audio_transcribe")
    assert [m.id for m in chain] == ["wb"]  # explicit id → no failover chain


# --------------------------------------------------------------------------- #
# _proxy_audio — failover loop
# --------------------------------------------------------------------------- #
def test_failover_on_connection_error(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wb"]})
    calls = []

    def handler(url, kwargs):
        calls.append(url)
        if ":9001" in url:
            raise httpx.ConnectError("connection refused")
        return _FakeResp(200, b'{"text":"served by wb"}')

    monkeypatch.setattr(server_audio, "get_async_client", lambda: _FakeClient(handler))
    resp = _proxy(_FakeReq())
    assert resp.status_code == 200
    assert b"served by wb" in resp.body
    assert any(":9001" in u for u in calls) and any(":9002" in u for u in calls)


def test_failover_on_503(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wb"]})

    def handler(url, kwargs):
        return _FakeResp(503) if ":9001" in url else _FakeResp(200, b'{"text":"wb"}')

    monkeypatch.setattr(server_audio, "get_async_client", lambda: _FakeClient(handler))
    resp = _proxy(_FakeReq())
    assert resp.status_code == 200 and b"wb" in resp.body


def test_happy_path_no_second_call(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wb"]})
    calls = []

    def handler(url, kwargs):
        calls.append(url)
        return _FakeResp(200, b'{"text":"wa"}')

    monkeypatch.setattr(server_audio, "get_async_client", lambda: _FakeClient(handler))
    resp = _proxy(_FakeReq())
    assert resp.status_code == 200 and b"wa" in resp.body
    assert len(calls) == 1 and ":9001" in calls[0]  # primary served, no failover call


def test_all_down_raises_last_error(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wb"]})

    def handler(url, kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(server_audio, "get_async_client", lambda: _FakeClient(handler))
    with pytest.raises(HTTPException) as ei:
        _proxy(_FakeReq())
    assert ei.value.status_code == 503


def test_client_error_not_failed_over(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wb"]})
    calls = []

    def handler(url, kwargs):
        calls.append(url)
        return _FakeResp(400, b'{"error":"bad audio"}')  # real client error on wa

    monkeypatch.setattr(server_audio, "get_async_client", lambda: _FakeClient(handler))
    resp = _proxy(_FakeReq())
    assert resp.status_code == 400  # returned as-is
    assert len(calls) == 1  # wb never tried — 4xx is not an availability failure


def test_explicit_model_down_does_not_fail_over(tmp_path, monkeypatch):
    _two_whisper_config(tmp_path, monkeypatch, transcribe={"model_id": "wa", "fallback": ["wb"]})
    calls = []

    def handler(url, kwargs):
        calls.append(url)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(server_audio, "get_async_client", lambda: _FakeClient(handler))
    req = _FakeReq(body=b'Content-Disposition: form-data; name="model"\r\n\r\nwb\r\n')
    with pytest.raises(HTTPException):
        _proxy(req)
    assert all(":9002" in u for u in calls) and all(":9001" not in u for u in calls)
