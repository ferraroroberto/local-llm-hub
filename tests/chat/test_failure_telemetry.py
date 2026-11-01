"""Every model call leaves one ring entry and one trace, whatever its outcome.

Each test drives the real hub ASGI app (``src.server.app``) with fake backends:
the subscription CLIs, the llama-server HTTP client and the audio proxies'
HTTP clients are replaced, never a live port. The app is wrapped in the OTel
ASGI middleware over an in-memory exporter, which is the server span the
FastAPI instrumentation opens in the running hub, so a test can read back both
records a failure must leave: the ``/admin`` request ring (Activity > Errors)
and the Langfuse trace (the exported server span).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from src import chat_translation, claude_cli, on_demand, openai_upstream
from src import server as server_mod
from src.audio import server_audio_asr, server_audio_tts, transcription_glossary
from src.hub_observability import OBS
from src.routes import anthropic_messages, openai_chat

_EXPORTER = InMemorySpanExporter()


def _recording_provider() -> TracerProvider:
    """A provider that records even though the suite sets ``OTEL_SDK_DISABLED``.

    The SDK reads that switch once, when the provider is built, and hands out
    no-op tracers if it is on; the conftest turns it on for every test.
    """
    saved = os.environ.pop("OTEL_SDK_DISABLED", None)
    try:
        provider = TracerProvider()
    finally:
        if saved is not None:
            os.environ["OTEL_SDK_DISABLED"] = saved
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    return provider


_PROVIDER = _recording_provider()

_SECRET = "SECRET-PROMPT-TEXT"


@pytest.fixture
def hub(monkeypatch, write_config):
    """A hub with one local row per backend family, behind a recording tracer."""
    write_config({
        "hub": {"port": 8000},
        "hosts": {
            "pc": {
                "platform": "win32",
                "default": True,
                "enabled": ["haiku", "flash", "llama", "asr", "voice"],
            },
        },
        "models": {
            "haiku": {"display_name": "claude-haiku-test", "backend": "claude"},
            "flash": {"display_name": "gemini-flash-test", "backend": "gemini"},
            "llama": {"display_name": "llama-test", "backend": "openai",
                      "engine": "llama-server", "port": 9101},
            "asr": {"display_name": "asr-test", "backend": "whisper",
                    "engine": "whisper-server", "port": 9102},
            "voice": {"display_name": "voice-test", "backend": "tts",
                      "engine": "tts-server", "port": 9103,
                      "aliases": ["audio_speech"]},
        },
        "roles": {"audio": {"transcribe": {"model_id": "asr"}}},
    })
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc")
    monkeypatch.delenv("OTEL_HASH_PROMPTS", raising=False)
    monkeypatch.setattr(on_demand, "ensure_ready", lambda model, *a, **k: None)
    monkeypatch.setattr(transcription_glossary, "load_rules", lambda: [])
    _EXPORTER.clear()
    app = OpenTelemetryMiddleware(server_mod.app, tracer_provider=_PROVIDER)
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class _SyncClient:
    """Stand-in for the pooled httpx client llama-server calls go through."""

    def __init__(self, *, exc: Optional[Exception] = None, status: int = 200,
                 body: Any = None, stream_lines: Optional[List[str]] = None) -> None:
        self.exc = exc
        self.status = status
        self.body = body
        self.stream_lines = stream_lines or []

    def _response(self) -> httpx.Response:
        if isinstance(self.body, (dict, list)):
            return httpx.Response(self.status, json=self.body)
        return httpx.Response(self.status, text=str(self.body or ""))

    def post(self, url, **kwargs):
        if self.exc is not None:
            raise self.exc
        return self._response()

    def stream(self, method, url, **kwargs):
        client = self

        class _CM:
            def __enter__(self_inner):
                if client.exc is not None:
                    raise client.exc
                return _StreamResp(client.status, client.stream_lines, str(client.body or ""))

            def __exit__(self_inner, *a):
                return False

        return _CM()


class _StreamResp:
    def __init__(self, status: int, lines: List[str], body: str) -> None:
        self.status_code = status
        self._lines = lines
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def iter_lines(self):
        yield from self._lines


class _AsyncClient:
    """Stand-in for the pooled async httpx client the audio proxies use."""

    def __init__(self, *, exc: Optional[Exception] = None, status: int = 200,
                 content: bytes = b"{}") -> None:
        self.exc = exc
        self.status = status
        self.content = content

    async def post(self, url, **kwargs):
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status, content=self.content,
                              headers={"content-type": "application/json"})


def _ok_llama(text: str = "pong") -> Dict[str, Any]:
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "model": "llama-test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


def _ok_cli(prompt, **kwargs) -> Dict[str, Any]:
    return {"type": "result", "is_error": False, "result": "ok",
            "stop_reason": "end_turn", "usage": {"input_tokens": 2, "output_tokens": 1}}


# --------------------------------------------------------------------------- #
# readers
# --------------------------------------------------------------------------- #
def _marker() -> str:
    return f"m-{uuid.uuid4().hex[:10]}"


def _traceparent(trace_hex: str) -> str:
    return f"00-{trace_hex}-{uuid.uuid4().hex[:16]}-01"


def _call(client: TestClient, method: str, path: str, **kwargs) -> tuple:
    """Send one request; return ``(response, ring_row, server_span)``.

    The ring has no request id, so every call carries its own W3C
    ``traceparent``; the server span continues that trace and the ring row
    must carry the same trace id, which is exactly the ring-to-trace link
    under test.
    """
    trace_hex = uuid.uuid4().hex
    headers = {"traceparent": _traceparent(trace_hex), **kwargs.pop("headers", {})}
    resp = client.request(method, path, headers=headers, **kwargs)
    resp.read()
    rows = [r for r in OBS.recent_requests(limit=200) if r["trace_id"] == trace_hex]
    spans = [
        s for s in _EXPORTER.get_finished_spans()
        if s.kind == SpanKind.SERVER and format(s.context.trace_id, "032x") == trace_hex
    ]
    assert len(rows) == 1, f"expected one ring entry for {path}, got {len(rows)}: {rows}"
    assert len(spans) == 1, f"expected one server span for {path}, got {len(spans)}"
    return resp, rows[0], spans[0]


def _assert_failure(row: Dict[str, Any], span: Any, *, status: int,
                    error_class: str, model: str = "") -> None:
    assert row["status"] == status, row
    assert row["error_class"] == error_class, row
    assert row["error_detail"], row
    if model:
        assert row["model"] == model, row
    attrs = dict(span.attributes or {})
    assert attrs.get("hub.error.class") == error_class, attrs
    assert attrs.get("langfuse.observation.level") == "ERROR", attrs
    assert attrs.get("langfuse.observation.status_message"), attrs
    if model:
        assert attrs.get("gen_ai.request.model") == model, attrs
    errors = OBS.recent_errors(limit=200)
    assert any(e["trace_id"] == row["trace_id"] for e in errors), "missing from Activity > Errors"


def _messages_body(model: str, **extra) -> Dict[str, Any]:
    return {"model": model, "max_tokens": 16,
            "messages": [{"role": "user", "content": _SECRET}], **extra}


def _chat_body(model: str, **extra) -> Dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": _SECRET}], **extra}


def _multipart(model: str) -> Dict[str, Any]:
    return {"files": {"file": ("a.wav", b"RIFF0000", "audio/wav")}, "data": {"model": model}}


# --------------------------------------------------------------------------- #
# hub-side rejections: 4xx
# --------------------------------------------------------------------------- #
def test_unknown_model_is_a_bad_request_with_a_trace(hub):
    model = _marker()
    _, row, span = _call(hub, "POST", "/v1/messages", json=_messages_body(model))
    _assert_failure(row, span, status=400, error_class="bad_request", model=model)


def test_validation_failure_is_a_bad_request_with_a_trace(hub):
    _, row, span = _call(hub, "POST", "/v1/chat/completions", json={"model": "llama"})
    _assert_failure(row, span, status=422, error_class="bad_request", model="llama")
    assert "messages" in row["error_detail"]


def test_bearer_rejection_is_recorded(hub, monkeypatch):
    """A proxied caller without the token is a failed call, not a silent one."""
    import types

    from src import webapp_config

    monkeypatch.setattr(webapp_config, "load_webapp_config",
                        lambda: types.SimpleNamespace(auth_token="tok"))
    _, row, span = _call(hub, "POST", "/v1/messages", json=_messages_body("haiku"),
                         headers={"X-Forwarded-For": "100.64.0.9"})
    _assert_failure(row, span, status=401, error_class="unauthorized", model="haiku")


# --------------------------------------------------------------------------- #
# subscription CLIs
# --------------------------------------------------------------------------- #
def test_claude_cli_timeout_is_distinct(hub, monkeypatch):
    def boom(prompt, **kwargs):
        raise claude_cli.ClaudeCLIError("claude -p timed out after 600s", error_class="timeout")

    monkeypatch.setattr(chat_translation, "call_claude", boom)
    _, row, span = _call(hub, "POST", "/v1/messages", json=_messages_body("haiku"))
    _assert_failure(row, span, status=502, error_class="timeout", model="haiku")
    assert "timed out" in row["error_detail"]


def test_claude_cli_nonzero_exit_is_an_upstream_error(hub, monkeypatch):
    def boom(prompt, **kwargs):
        raise claude_cli.ClaudeCLIError("claude -p exited 1: auth failure")

    monkeypatch.setattr(openai_chat, "call_claude", boom)
    _, row, span = _call(hub, "POST", "/v1/chat/completions", json=_chat_body("haiku"))
    _assert_failure(row, span, status=502, error_class="upstream_error", model="haiku")
    assert "exited 1" in row["error_detail"]


def test_claude_cli_real_timeout_is_tagged(monkeypatch):
    """The real wrapper tags its own timeout, not only a test double."""
    import subprocess

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(claude_cli.subprocess, "run", raise_timeout)
    with pytest.raises(claude_cli.ClaudeCLIError) as exc:
        claude_cli.call_claude("hi", model="claude-haiku-test", timeout=1)
    assert exc.value.error_class == "timeout"


def test_unhandled_exception_is_an_internal_error(hub, monkeypatch):
    def boom(prompt, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(chat_translation, "call_gemini", boom)
    _, row, span = _call(hub, "POST", "/v1/messages", json=_messages_body("flash"))
    _assert_failure(row, span, status=500, error_class="internal_error", model="flash")
    assert span.status.status_code == StatusCode.ERROR


def test_claude_stream_failure_is_recorded(hub, monkeypatch):
    def records(*a, **k):
        raise claude_cli.ClaudeCLIError("claude -p stream exited 2: boom")
        yield  # pragma: no cover — makes this a generator

    monkeypatch.setattr(anthropic_messages, "call_claude_stream", records)
    _, row, span = _call(hub, "POST", "/v1/messages",
                         json=_messages_body("haiku", stream=True))
    _assert_failure(row, span, status=200, error_class="upstream_error", model="haiku")
    assert span.status.status_code == StatusCode.ERROR


# --------------------------------------------------------------------------- #
# llama-server (open-weight) backends
# --------------------------------------------------------------------------- #
def test_llama_server_down_is_distinct(hub, monkeypatch):
    monkeypatch.setattr(openai_upstream, "get_sync_client",
                        lambda: _SyncClient(exc=httpx.ConnectError("refused")))
    _, row, span = _call(hub, "POST", "/v1/chat/completions", json=_chat_body("llama"))
    _assert_failure(row, span, status=502, error_class="backend_down", model="llama")


def test_llama_server_timeout_is_distinct(hub, monkeypatch):
    monkeypatch.setattr(openai_upstream, "get_sync_client",
                        lambda: _SyncClient(exc=httpx.ReadTimeout("slow")))
    _, row, span = _call(hub, "POST", "/v1/messages", json=_messages_body("llama"))
    _assert_failure(row, span, status=502, error_class="timeout", model="llama")
    assert "timed out" in row["error_detail"]


def test_llama_server_http_error_is_an_upstream_error(hub, monkeypatch):
    monkeypatch.setattr(openai_upstream, "get_sync_client",
                        lambda: _SyncClient(status=500, body="model crashed"))
    _, row, span = _call(hub, "POST", "/v1/chat/completions", json=_chat_body("llama"))
    _assert_failure(row, span, status=502, error_class="upstream_error", model="llama")
    assert "HTTP 500" in row["error_detail"]


def test_llama_server_stream_down_is_recorded(hub, monkeypatch):
    """The passthrough stream used to record a clean 200: no error, no Errors row."""
    monkeypatch.setattr(openai_upstream, "get_sync_client",
                        lambda: _SyncClient(exc=httpx.ConnectError("refused")))
    _, row, span = _call(hub, "POST", "/v1/chat/completions",
                         json=_chat_body("llama", stream=True))
    _assert_failure(row, span, status=200, error_class="backend_down", model="llama")


def test_backend_not_loaded_is_distinct(hub, monkeypatch):
    def not_ready(model, *a, **k):
        raise on_demand.OnDemandNotReady("llama did not become ready within 120s")

    monkeypatch.setattr(on_demand, "ensure_ready", not_ready)
    _, row, span = _call(hub, "POST", "/v1/chat/completions", json=_chat_body("llama"))
    _assert_failure(row, span, status=503, error_class="backend_not_loaded", model="llama")


# --------------------------------------------------------------------------- #
# audio
# --------------------------------------------------------------------------- #
def test_tts_down_is_recorded(hub, monkeypatch):
    """/v1/audio/speech used to be outside the ring entirely."""
    monkeypatch.setattr(server_audio_tts, "get_async_client",
                        lambda: _AsyncClient(exc=httpx.ConnectError("refused")))
    _, row, span = _call(hub, "POST", "/v1/audio/speech",
                         json={"model": "voice", "input": _SECRET})
    _assert_failure(row, span, status=503, error_class="backend_down", model="voice")


def test_tts_success_is_recorded(hub, monkeypatch):
    monkeypatch.setattr(server_audio_tts, "get_async_client",
                        lambda: _AsyncClient(content=b"RIFF"))
    _, row, span = _call(hub, "POST", "/v1/audio/speech",
                         json={"model": "voice", "input": "hello"})
    assert row["status"] == 200 and row["error_class"] == "" and row["served_model"] == "voice"
    assert "hub.error.class" not in (span.attributes or {})


def test_asr_timeout_is_distinct(hub, monkeypatch):
    monkeypatch.setattr(server_audio_asr, "get_async_client",
                        lambda: _AsyncClient(exc=httpx.ReadTimeout("slow")))
    _, row, span = _call(hub, "POST", "/v1/audio/transcriptions", **_multipart("asr"))
    _assert_failure(row, span, status=502, error_class="timeout", model="asr")


def test_asr_down_is_distinct(hub, monkeypatch):
    monkeypatch.setattr(server_audio_asr, "get_async_client",
                        lambda: _AsyncClient(exc=httpx.ConnectError("refused")))
    _, row, span = _call(hub, "POST", "/v1/audio/transcriptions", **_multipart("asr"))
    _assert_failure(row, span, status=503, error_class="backend_down", model="asr")


# --------------------------------------------------------------------------- #
# client disconnect mid-stream
# --------------------------------------------------------------------------- #
class _Endless:
    """A backend stream that never ends on its own."""

    def __init__(self, item: Any) -> None:
        self.item = item

    def __iter__(self):
        return self

    def __next__(self):
        return self.item


_CLAUDE_DELTA = {
    "type": "stream_event",
    "event": {"type": "content_block_delta", "index": 0,
              "delta": {"type": "text_delta", "text": "more"}},
}


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/messages"])
def test_client_disconnect_mid_stream_is_recorded(hub, monkeypatch, path):
    monkeypatch.setattr(openai_chat, "call_openai_chat_stream",
                        lambda *a, **k: _Endless('data: {"choices":[{"delta":{"content":"x"}}]}'))
    monkeypatch.setattr(anthropic_messages, "call_claude_stream",
                        lambda *a, **k: _Endless(_CLAUDE_DELTA))
    trace_hex = uuid.uuid4().hex
    if path == "/v1/messages":
        body = json.dumps(_messages_body("haiku", stream=True)).encode()
    else:
        body = json.dumps(_chat_body("llama", stream=True)).encode()
    app = OpenTelemetryMiddleware(server_mod.app, tracer_provider=_PROVIDER)

    async def drive() -> None:
        body_started = asyncio.Event()
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await body_started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                body_started.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": path, "raw_path": path.encode(),
            "root_path": "", "query_string": b"",
            "headers": [(b"content-type", b"application/json"),
                        (b"traceparent", _traceparent(trace_hex).encode())],
            "client": ("127.0.0.1", 5000), "server": ("127.0.0.1", 8000),
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=10)

    asyncio.run(drive())
    rows = [r for r in OBS.recent_requests(limit=200) if r["trace_id"] == trace_hex]
    assert len(rows) == 1, rows
    assert rows[0]["error_class"] == "client_disconnect", rows[0]
    spans = [s for s in _EXPORTER.get_finished_spans()
             if s.kind == SpanKind.SERVER and format(s.context.trace_id, "032x") == trace_hex]
    assert len(spans) == 1
    assert (spans[0].attributes or {}).get("hub.error.class") == "client_disconnect"


# --------------------------------------------------------------------------- #
# successes: every backend family gets a trace and its served model
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model,path,stream", [
    ("haiku", "/v1/messages", False),
    ("haiku", "/v1/chat/completions", False),
    ("flash", "/v1/messages", False),
    ("llama", "/v1/messages", False),
    ("llama", "/v1/chat/completions", False),
    ("llama", "/v1/chat/completions", True),
])
def test_success_matrix_has_a_trace_and_served_model(hub, monkeypatch, model, path, stream):
    monkeypatch.setattr(chat_translation, "call_claude", _ok_cli)
    monkeypatch.setattr(chat_translation, "call_gemini", _ok_cli)
    monkeypatch.setattr(openai_chat, "call_claude", _ok_cli)
    monkeypatch.setattr(openai_upstream, "get_sync_client", lambda: _SyncClient(
        body=_ok_llama(),
        stream_lines=['data: {"choices":[{"delta":{"content":"hi"}}]}', "data: [DONE]"],
    ))
    body = _messages_body(model) if path == "/v1/messages" else _chat_body(model)
    body["stream"] = stream
    _, row, span = _call(hub, "POST", path, json=body)
    assert row["status"] == 200 and row["error_class"] == "" and row["error_detail"] == "", row
    assert row["served_model"] == model, row
    attrs = dict(span.attributes or {})
    assert attrs.get("gen_ai.request.model") == model
    assert attrs.get("gen_ai.response.model") == model
    assert "hub.error.class" not in attrs


_LLAMA_TIMINGS = {"cache_n": 2, "prompt_n": 10, "predicted_n": 8}
_LLAMA_USAGE = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}


@pytest.mark.parametrize("final_chunk,expected", [
    # llama-server's default: no usage block, only timings on the last chunk.
    ({"choices": [{"delta": {}, "finish_reason": "length"}], "timings": _LLAMA_TIMINGS}, (12, 8)),
    # an upstream that does send the usage chunk.
    ({"choices": [], "usage": _LLAMA_USAGE}, (12, 8)),
])
def test_llama_stream_records_token_counts(hub, monkeypatch, final_chunk, expected):
    monkeypatch.setattr(openai_upstream, "get_sync_client", lambda: _SyncClient(
        stream_lines=[
            'data: {"choices":[{"delta":{"content":"hi"}}]}',
            "data: " + json.dumps(final_chunk),
            "data: [DONE]",
        ],
    ))
    _, row, _ = _call(hub, "POST", "/v1/chat/completions",
                      json=_chat_body("llama", stream=True))
    assert (row["in_tok"], row["out_tok"]) == expected, row


def test_llama_buffered_call_records_token_counts(hub, monkeypatch):
    monkeypatch.setattr(openai_upstream, "get_sync_client",
                        lambda: _SyncClient(body=_ok_llama()))
    _, row, _ = _call(hub, "POST", "/v1/chat/completions", json=_chat_body("llama"))
    assert (row["in_tok"], row["out_tok"]) == (3, 1), row


# --------------------------------------------------------------------------- #
# OTEL_HASH_PROMPTS: no prompt or vendor body in error telemetry
# --------------------------------------------------------------------------- #
def _all_span_text(span: Any) -> str:
    return json.dumps({k: str(v) for k, v in (span.attributes or {}).items()}) + str(
        span.status.description or ""
    )


def test_hashing_keeps_vendor_body_out_of_error_telemetry(hub, monkeypatch):
    monkeypatch.setenv("OTEL_HASH_PROMPTS", "true")
    monkeypatch.setattr(openai_upstream, "get_sync_client",
                        lambda: _SyncClient(status=500, body=f"echo: {_SECRET}"))
    _, row, span = _call(hub, "POST", "/v1/chat/completions", json=_chat_body("llama"))
    _assert_failure(row, span, status=502, error_class="upstream_error", model="llama")
    assert _SECRET not in row["error_detail"]
    assert _SECRET not in _all_span_text(span)


def test_hashing_keeps_cli_stderr_out_of_error_telemetry(hub, monkeypatch):
    monkeypatch.setenv("OTEL_HASH_PROMPTS", "true")

    def boom(prompt, **kwargs):
        raise claude_cli.ClaudeCLIError(f"claude -p exited 1: {_SECRET}")

    monkeypatch.setattr(chat_translation, "call_claude", boom)
    _, row, span = _call(hub, "POST", "/v1/messages", json=_messages_body("haiku"))
    _assert_failure(row, span, status=502, error_class="upstream_error", model="haiku")
    assert _SECRET not in row["error_detail"]
    assert _SECRET not in _all_span_text(span)


def test_without_hashing_the_error_text_is_kept(hub, monkeypatch):
    monkeypatch.setattr(openai_upstream, "get_sync_client",
                        lambda: _SyncClient(status=500, body="model crashed"))
    _, row, _ = _call(hub, "POST", "/v1/chat/completions", json=_chat_body("llama"))
    assert "model crashed" in row["error_detail"]
