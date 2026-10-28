"""Response compression across the hub (vendored ``StreamSafeGZipMiddleware``).

The entry document and the JS/CSS bundle are the bulk of a cold phone load, so
the hub gzips them. Streams and media must stay out: gzip buffers a live
SSE/audio stream and burns CPU on bytes that are already compressed. The
middleware is registered once, outermost on the parent app, so it covers the
mounted /admin sub-app and the /v1 routes alike.
"""

from __future__ import annotations

import asyncio
import threading

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from app_web import server as admin_server
from app_web.routers import machines as machines_router
from app_web.routers._helpers import sse_stream
from src import server as server_mod
from src.compression import StreamSafeGZipMiddleware
from src.routes import openai_chat as openai_chat_mod
from tests.chat.test_think_stripping import _delta, _sse_lines
from tests.routers.test_playground_router import _mock_stream_upstream

_GZIP = {"Accept-Encoding": "gzip"}


def _middleware_classes(app) -> list:
    return [m.cls for m in app.user_middleware]


def test_compression_is_outermost_and_the_only_gzip_layer():
    # user_middleware[0] wraps every later entry, so index 0 is outermost.
    assert _middleware_classes(server_mod.app)[0] is StreamSafeGZipMiddleware
    assert GZipMiddleware not in _middleware_classes(server_mod.app)
    assert GZipMiddleware not in _middleware_classes(admin_server.create_app())


def test_large_hub_json_is_gzipped():
    # The /v1 surface sits outside the /admin sub-app; the OpenAPI document is
    # the largest text body the parent app serves without a backend.
    r = TestClient(server_mod.app).get("/openapi.json", headers=_GZIP)
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert r.json()["info"]["title"]


def test_identity_client_gets_the_plain_body():
    r = TestClient(server_mod.app).get(
        "/openapi.json", headers={"Accept-Encoding": "identity"}
    )
    assert r.status_code == 200
    assert "content-encoding" not in r.headers


async def _first_body_arrives(app, method: str, path: str, *, body: bytes = b"",
                              content_type: str = "", on_first=None):
    """Send one request and return ``(response_headers, first_body)`` as soon as
    the first non-empty body message reaches the client, or ``first_body=None``
    if none does within the window. The app keeps running until then, so a
    layer that holds chunks back shows up as a missing first chunk."""
    headers = [(b"accept-encoding", b"gzip"), (b"host", b"test")]
    if content_type:
        headers.append((b"content-type", content_type.encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "scheme": "http",
        "headers": headers, "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
    }
    sent_request = False
    arrived: asyncio.Future = asyncio.get_running_loop().create_future()
    response_headers: dict = {}

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            response_headers.update(
                {k.decode().lower(): v.decode() for k, v in message["headers"]}
            )
        elif message["type"] == "http.response.body" and message.get("body"):
            if not arrived.done():
                arrived.set_result(message["body"])
                if on_first:
                    on_first()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        first = await asyncio.wait_for(asyncio.shield(arrived), timeout=3)
    except asyncio.TimeoutError:
        first = None
    finally:
        task.cancel()
    return response_headers, first


def test_admin_event_stream_delivers_its_first_frame_at_once():
    headers, first = asyncio.run(
        _first_body_arrives(server_mod.app, "GET", "/admin/api/hub/log/tail")
    )
    assert first is not None, "SSE head or first frame was held back"
    assert headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in headers
    assert first.startswith(b":open")


def test_completion_event_stream_delivers_each_frame_as_produced(monkeypatch):
    # The upstream releases its second frame only after the client has seen the
    # first one, so a layer that holds chunks back leaves ``first`` empty.
    first_seen = threading.Event()

    def fake_stream(base_url, model, messages, **kwargs):
        lines = list(_sse_lines(_delta("Hello "), _delta("world!")))
        yield lines[0]
        first_seen.wait(timeout=5)
        yield from lines[1:]

    monkeypatch.setattr(openai_chat_mod, "call_openai_chat_stream", fake_stream)
    headers, first = asyncio.run(
        _first_body_arrives(
            server_mod.app, "POST", "/v1/chat/completions",
            body=(b'{"model": "qwen3.5-4b", "stream": true, '
                  b'"messages": [{"role": "user", "content": "hi"}]}'),
            content_type="application/json", on_first=first_seen.set,
        )
    )
    assert first is not None, "first SSE chunk was held back"
    assert headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in headers


def test_websocket_handshake_passes_through_compression(monkeypatch):
    async def _refuse(*_a, **_kw):
        return {"ok": False, "error": "stubbed in tests"}

    monkeypatch.setattr(machines_router.ssh_terminal, "create_ssh_session", _refuse)
    with TestClient(server_mod.app) as client:
        with client.websocket_connect(
            "/admin/api/machines/tower/terminal", headers=_GZIP
        ) as ws:
            assert ws.receive_json()["type"] == "error"


def test_entry_document_is_gzipped():
    r = TestClient(server_mod.app).get("/admin/", headers=_GZIP)
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert "<html" in r.text


def test_large_static_script_is_gzipped():
    r = TestClient(server_mod.app).get(
        "/admin/static/_vendored/xterm/xterm.js", headers=_GZIP
    )
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"


def test_revalidation_stays_bodyless_and_unencoded():
    # A 304 carries no body, so it must not gain a gzip trailer or a
    # Content-Encoding header on its way out through the compression layer.
    client = TestClient(server_mod.app)
    etag = client.get("/admin/", headers=_GZIP).headers["etag"]
    r = client.get("/admin/", headers={**_GZIP, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert "content-encoding" not in r.headers


def test_small_json_stays_uncompressed():
    # Behind the hub's BaseHTTPMiddleware layers every 200 body arrives
    # re-streamed with no Content-Length; the size minimum must still hold.
    r = TestClient(server_mod.app).get("/admin/api/healthz", headers=_GZIP)
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert r.json()


def test_streamed_audio_is_not_gzipped(monkeypatch):
    _mock_stream_upstream(monkeypatch, chunk=b"\x01\x00" * 2000)
    r = TestClient(server_mod.app).post(
        "/admin/api/playground/speak",
        data={"model": "chatterbox-tts", "input": "hi", "stream": "true",
              "response_format": "pcm"},
        headers=_GZIP,
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("content-encoding") in (None, "identity")


def test_empty_sse_stream_opens_immediately_under_gzip():
    # The compression middleware holds ``http.response.start`` until the first
    # body chunk.
    # With nothing to seed, a stream would stay "connecting" until the first
    # 10 s keepalive, so sse_stream sends a comment frame right away.
    app = FastAPI()
    app.add_middleware(StreamSafeGZipMiddleware)

    @app.get("/stream")
    async def stream(request: Request):
        return sse_stream(request, asyncio.Queue, lambda q: None, seed=[])

    async def first_message() -> str:
        sent: list = []
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "path": "/stream", "raw_path": b"/stream",
            "query_string": b"", "root_path": "", "scheme": "http",
            "headers": [(b"accept-encoding", b"gzip"), (b"host", b"test")],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
        }

        async def receive():
            await asyncio.sleep(3600)

        async def send(message):
            sent.append(message["type"])

        task = asyncio.create_task(app(scope, receive, send))
        await asyncio.sleep(0.5)
        seen = list(sent)
        task.cancel()
        return seen

    sent = asyncio.run(first_message())
    assert "http.response.start" in sent
