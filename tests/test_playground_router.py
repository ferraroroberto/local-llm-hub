"""Unit tests for app_web/routers/playground.py attachment handling.

The Playground route proxies in-process to the hub's /v1/messages. Here
we mock that httpx call and assert the route builds the right content
block for the uploaded file: a `document` block for PDFs, an `image`
block otherwise. The real document->agy path is covered end-to-end by
tests/test_image_blocks.py + the server extractor.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src import server as server_mod

# Tiny valid single-page PDF (no extractable text needed — we only assert
# the block shape the route hands upstream).
_TINY_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)
_PNG = b"\x89PNG\r\n\x1a\nfake-bytes"


def _mock_upstream(monkeypatch) -> dict:
    """Patch httpx.AsyncClient so the proxied payload is captured, not sent."""
    captured: dict = {}

    class _FakeResp:
        is_success = True
        status_code = 200
        text = ""

        def json(self):
            return {
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            captured["url"] = url
            captured["payload"] = json
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return captured


def _attachment_block(captured: dict) -> dict:
    content = captured["payload"]["messages"][0]["content"]
    media = [b for b in content if b.get("type") != "text"]
    assert len(media) == 1, content
    return media[0]


def test_playground_pdf_builds_document_block(monkeypatch):
    captured = _mock_upstream(monkeypatch)
    client = TestClient(server_mod.app)
    r = client.post(
        "/admin/api/playground/send",
        data={"model": "claude-haiku-4-5", "prompt": "summarize", "max_tokens": "64"},
        files={"attachment": ("report.pdf", _TINY_PDF, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    block = _attachment_block(captured)
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"
    assert block["source"]["type"] == "base64"


def test_playground_image_still_builds_image_block(monkeypatch):
    captured = _mock_upstream(monkeypatch)
    client = TestClient(server_mod.app)
    r = client.post(
        "/admin/api/playground/send",
        data={"model": "claude-haiku-4-5", "prompt": "describe", "max_tokens": "64"},
        files={"attachment": ("pic.png", _PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    block = _attachment_block(captured)
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"


def test_playground_json_builds_document_block(monkeypatch):
    captured = _mock_upstream(monkeypatch)
    client = TestClient(server_mod.app)
    r = client.post(
        "/admin/api/playground/send",
        data={"model": "claude-haiku-4-5", "prompt": "what's in here?",
              "max_tokens": "64"},
        files={"attachment": ("data.json", b'{"k": 1}', "application/json")},
    )
    assert r.status_code == 200, r.text
    block = _attachment_block(captured)
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/json"


def test_playground_no_attachment_is_text_only(monkeypatch):
    captured = _mock_upstream(monkeypatch)
    client = TestClient(server_mod.app)
    r = client.post(
        "/admin/api/playground/send",
        data={"model": "claude-haiku-4-5", "prompt": "hi", "max_tokens": "64"},
    )
    assert r.status_code == 200, r.text
    content = captured["payload"]["messages"][0]["content"]
    assert content == [{"type": "text", "text": "hi"}]
