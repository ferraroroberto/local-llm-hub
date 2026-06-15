"""Unit tests for POST /v1/images/generations — `agy` is mocked.

We monkeypatch `call_gemini_image` so no real Antigravity CLI / Imagen call
happens; the tests assert the OpenAI-shape contract and the routing guards.
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from src import server as server_mod
from src.gemini_cli import GeminiCLIError

# A 1x1 PNG — enough to round-trip through base64 in the response.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)


def test_images_generation_returns_b64(monkeypatch):
    seen = {}

    def fake_call(prompt, *, reference_image=None, timeout=None):
        seen["prompt"] = prompt
        return {
            "image_bytes": _PNG_BYTES,
            "media_type": "image/png",
            "result_text": "SAVED",
        }

    monkeypatch.setattr(server_mod, "call_gemini_image", fake_call)

    client = TestClient(server_mod.app)
    r = client.post(
        "/v1/images/generations",
        json={"model": "gemini_image", "prompt": "a red apple"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "created" in body
    assert len(body["data"]) == 1
    assert base64.b64decode(body["data"][0]["b64_json"]) == _PNG_BYTES
    assert seen["prompt"] == "a red apple"


def test_images_edit_returns_b64(monkeypatch):
    seen = {}

    def fake_call(prompt, *, reference_image=None, timeout=None):
        seen["prompt"] = prompt
        seen["has_ref"] = reference_image is not None
        return {
            "image_bytes": _PNG_BYTES,
            "media_type": "image/png",
            "result_text": "SAVED",
        }

    monkeypatch.setattr(server_mod, "call_gemini_image", fake_call)

    client = TestClient(server_mod.app)
    r = client.post(
        "/v1/images/edits",
        data={"model": "gemini_image", "prompt": "make it blue"},
        files={"image": ("duck.png", _PNG_BYTES, "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert base64.b64decode(body["data"][0]["b64_json"]) == _PNG_BYTES
    assert seen["prompt"] == "make it blue"
    assert seen["has_ref"] is True


def test_images_edit_rejects_non_image_model():
    client = TestClient(server_mod.app)
    r = client.post(
        "/v1/images/edits",
        data={"model": "gemini_flash", "prompt": "x"},
        files={"image": ("duck.png", _PNG_BYTES, "image/png")},
    )
    assert r.status_code == 400


def test_images_generation_cli_error_returns_502(monkeypatch):
    def fake_call(prompt, *, size="1024x1024", timeout=300.0):
        raise GeminiCLIError("agy did not produce an image artifact")

    monkeypatch.setattr(server_mod, "call_gemini_image", fake_call)

    client = TestClient(server_mod.app)
    r = client.post(
        "/v1/images/generations",
        json={"model": "gemini_image", "prompt": "a red apple"},
    )
    assert r.status_code == 502


def test_images_generation_rejects_non_image_model():
    client = TestClient(server_mod.app)
    r = client.post(
        "/v1/images/generations",
        json={"model": "gemini_flash", "prompt": "a red apple"},
    )
    assert r.status_code == 400
    assert "image-generation" in r.json()["detail"]


def test_images_generation_rejects_n_gt_1(monkeypatch):
    monkeypatch.setattr(
        server_mod, "call_gemini_image",
        lambda *a, **k: {"image_bytes": _PNG_BYTES, "media_type": "image/png",
                         "result_text": "SAVED"},
    )
    client = TestClient(server_mod.app)
    r = client.post(
        "/v1/images/generations",
        json={"model": "gemini_image", "prompt": "x", "n": 2},
    )
    assert r.status_code == 400


def test_list_models_includes_gemini_image():
    client = TestClient(server_mod.app)
    r = client.get("/v1/models")
    ids = {m["id"] for m in r.json()["data"]}
    assert "gemini_image" in ids


def test_collect_artifact_identifies_by_content_not_extension(tmp_path):
    """A .png file holding JPEG bytes must be reported as image/jpeg —
    `agy` was observed saving JPEG bytes under a .png name (issue #114)."""
    from src.gemini_cli import _collect_image_artifact

    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    (tmp_path / "generated.png").write_bytes(jpeg_bytes)
    (tmp_path / "notes.txt").write_text("not an image")

    data, media_type = _collect_image_artifact(tmp_path)
    assert data == jpeg_bytes
    assert media_type == "image/jpeg"


def test_collect_artifact_returns_none_when_no_image(tmp_path):
    from src.gemini_cli import _collect_image_artifact

    (tmp_path / "reply.txt").write_text("NO_IMAGE: cannot generate")
    data, media_type = _collect_image_artifact(tmp_path)
    assert data is None and media_type is None
