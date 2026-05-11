"""Image content-block routing for the claude-* and gemini-* CLI paths.

Covers the per-request temp-dir lifecycle, base64 decoding, URL fallback,
and the 400 raised when an image is routed at a text-only local backend.
The CLIs themselves are fully mocked — we just assert what the hub
hands them.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

from fastapi.testclient import TestClient

from src import server as server_mod


# 1x1 PNG (red pixel), valid bytes — base64-encoded for transport.
_RED_PIXEL_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0g"
    "AAAABJRU5ErkJggg=="
)


def _stub_envelope(text: str = "described"):
    return {
        "type": "result", "is_error": False, "result": text,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def test_claude_path_writes_image_and_passes_to_cli(monkeypatch):
    captured = {}

    def fake_call(prompt, *, model=None, system=None, images=None, timeout=600.0):
        captured["prompt"] = prompt
        captured["images"] = [Path(p) for p in (images or [])]
        # File must exist while the CLI is running.
        captured["image_bytes"] = [p.read_bytes() for p in captured["images"]]
        return _stub_envelope("ok")

    monkeypatch.setattr(server_mod, "call_claude", fake_call)

    client = TestClient(server_mod.app)
    r = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _RED_PIXEL_B64,
                }},
            ],
        }],
    })
    assert r.status_code == 200, r.text
    assert len(captured["images"]) == 1
    assert captured["images"][0].suffix == ".png"
    assert captured["image_bytes"][0] == base64.b64decode(_RED_PIXEL_B64)
    # Image reference was prepended to the flattened prompt seen by the
    # extractor's caller (server side flatten + claude_cli's prepend
    # both run; here we observe just what the hub passed in).
    assert "what is this?" in captured["prompt"]


def test_claude_path_temp_dir_cleaned_up_after_call(monkeypatch):
    """The per-request temp dir must not survive the response."""
    seen_path: dict = {}

    def fake_call(prompt, *, model=None, system=None, images=None, timeout=600.0):
        assert images and len(images) == 1
        seen_path["path"] = Path(images[0])
        assert seen_path["path"].exists()
        return _stub_envelope()

    monkeypatch.setattr(server_mod, "call_claude", fake_call)

    client = TestClient(server_mod.app)
    r = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": _RED_PIXEL_B64,
                }},
            ],
        }],
    })
    assert r.status_code == 200
    # After the response, the dir is gone.
    assert not seen_path["path"].exists()
    assert not seen_path["path"].parent.exists()


def test_gemini_path_writes_image_and_passes_to_cli(monkeypatch):
    captured = {}

    def fake_call(prompt, *, model=None, system=None, images=None, timeout=600.0):
        captured["model"] = model
        captured["images"] = [Path(p) for p in (images or [])]
        captured["exists"] = [p.exists() for p in captured["images"]]
        return _stub_envelope("g-described")

    monkeypatch.setattr(server_mod, "call_gemini", fake_call)

    client = TestClient(server_mod.app)
    r = client.post("/v1/messages", json={
        "model": "gemini-3.1-pro-preview",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": _RED_PIXEL_B64,
                }},
            ],
        }],
    })
    assert r.status_code == 200, r.text
    assert captured["model"] == "gemini-3.1-pro-preview"
    assert len(captured["images"]) == 1
    assert captured["images"][0].suffix == ".jpg"
    assert captured["exists"] == [True]


def test_multiple_images_extracted_in_order(monkeypatch):
    captured = {}

    def fake_call(prompt, *, model=None, system=None, images=None, timeout=600.0):
        captured["images"] = list(images or [])
        return _stub_envelope()

    monkeypatch.setattr(server_mod, "call_claude", fake_call)

    client = TestClient(server_mod.app)
    r = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": _RED_PIXEL_B64,
                }},
                {"type": "text", "text": "and"},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/webp", "data": _RED_PIXEL_B64,
                }},
            ],
        }],
    })
    assert r.status_code == 200
    paths = captured["images"]
    assert [Path(p).suffix for p in paths] == [".png", ".webp"]


def test_url_image_falls_back_to_text_reference(monkeypatch):
    captured = {}

    def fake_call(prompt, *, model=None, system=None, images=None, timeout=600.0):
        captured["prompt"] = prompt
        captured["images"] = images
        return _stub_envelope()

    monkeypatch.setattr(server_mod, "call_claude", fake_call)

    client = TestClient(server_mod.app)
    r = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image", "source": {
                    "type": "url",
                    "url": "https://example.com/cat.png",
                }},
            ],
        }],
    })
    assert r.status_code == 200
    # URL images aren't downloaded; they become a text reference.
    assert captured["images"] in (None, [], ())
    assert "https://example.com/cat.png" in captured["prompt"]


def test_local_backend_rejects_image_with_helpful_400():
    client = TestClient(server_mod.app)
    r = client.post("/v1/messages", json={
        "model": "qwen3.5-4b",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": _RED_PIXEL_B64,
                }},
            ],
        }],
    })
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "text-only" in detail
    assert "claude-*" in detail or "gemini-*" in detail


def test_bad_base64_returns_400(monkeypatch):
    monkeypatch.setattr(
        server_mod, "call_claude",
        lambda *a, **k: _stub_envelope(),
    )
    client = TestClient(server_mod.app)
    r = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": "!!!not-base64!!!",
                }},
            ],
        }],
    })
    assert r.status_code == 400
    assert "bad image block" in r.json()["detail"]


def test_no_images_skips_temp_dir(monkeypatch):
    """Text-only request should not pay the temp-dir cost."""
    captured = {}

    def fake_call(prompt, *, model=None, system=None, images=None, timeout=600.0):
        captured["images"] = images
        return _stub_envelope()

    monkeypatch.setattr(server_mod, "call_claude", fake_call)

    client = TestClient(server_mod.app)
    r = client.post("/v1/messages", json={
        "model": "claude-haiku-4-5",
        "messages": [{"role": "user", "content": "no images here"}],
    })
    assert r.status_code == 200
    # `images` arrives as None (or falsy) — no temp dir was created.
    assert not captured["images"]
