"""Browser WebM/Ogg recordings are decoded to wav before an ASR backend sees them.

The parakeet worker's ``afconvert`` cannot open WebM/Opus, so a Chrome or
Android ``MediaRecorder`` clip came back as a bare 400 while wav and mp4
transcribed. The hub decodes the two browser containers itself and forwards
every other format byte for byte.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import wave

import pytest

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

from fastapi.testclient import TestClient  # noqa: E402

from src import server as server_mod  # noqa: E402
from src.audio import audio_normalize  # noqa: E402
from src.no_window import NO_WINDOW  # noqa: E402

WEBM = b"\x1a\x45\xdf\xa3" + b"\x9fB\x86\x81\x01" + b"\x00" * 64
OGG = b"OggS\x00\x02" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


def _wav(frames: int = 100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


class _Upstream:
    status_code = 200
    content = b'{"text": "hello"}'
    headers = {"content-type": "application/json"}


@pytest.fixture
def upstream_calls(monkeypatch):
    calls: list = []

    class _Client:
        async def post(self, url, **kwargs):
            calls.append({"url": url, "kwargs": kwargs})
            return _Upstream()

    monkeypatch.setattr("src.audio.server_audio_asr.get_async_client", lambda: _Client())
    return calls


def _post(name: str, payload: bytes, content_type: str, route: str = "/v1/audio/transcriptions"):
    return TestClient(server_mod.app).post(
        route,
        files={"file": (name, payload, content_type)},
        data={"model": "whisper"},
    )


@pytest.mark.parametrize(
    "payload,expected",
    [(WEBM, "webm"), (OGG, "ogg"), (_wav(), None), (MP4, None), (b"", None)],
)
def test_container_of(payload, expected):
    assert audio_normalize.container_of(payload) == expected


@pytest.mark.parametrize(
    "name,payload,content_type,expected",
    [
        ("rec.webm", WEBM, "audio/webm;codecs=opus", True),
        ("blob", WEBM, "video/webm", True),
        ("rec.ogg", OGG, "audio/ogg", True),
        ("rec.wav", _wav(), "audio/wav", False),
        ("rec.mp4", MP4, "audio/mp4", False),
        # A webm label on bytes that are not webm is not decoded: the magic decides.
        ("rec.webm", _wav(), "audio/webm", False),
    ],
)
def test_body_needs_decode(name, payload, content_type, expected):
    import httpx

    request = httpx.Request(
        "POST", "http://x/", files={"file": (name, payload, content_type)}, data={"model": "whisper"},
    )
    assert audio_normalize.body_needs_decode(request.read()) is expected


def test_webm_is_decoded_to_wav_before_forwarding(monkeypatch, upstream_calls):
    decoded = _wav(50)
    seen: list = []

    def fake_decode(data, container):
        seen.append((data, container))
        return decoded

    monkeypatch.setattr(audio_normalize, "_decode_to_wav", fake_decode)

    resp = _post("recording.webm", WEBM, "audio/webm;codecs=opus")

    assert resp.status_code == 200
    assert seen == [(WEBM, "webm")]
    sent = upstream_calls[0]["kwargs"]
    assert sent["files"]["file"] == ("recording.wav", decoded, "audio/wav")
    assert sent["data"]["model"] == "whisper"
    assert "content" not in sent


def test_ogg_is_decoded_too(monkeypatch, upstream_calls):
    monkeypatch.setattr(audio_normalize, "_decode_to_wav", lambda data, container: _wav(10))
    assert _post("rec.ogg", OGG, "audio/ogg").status_code == 200
    assert upstream_calls[0]["kwargs"]["files"]["file"][0] == "rec.wav"


def test_translate_route_decodes_webm(monkeypatch, upstream_calls):
    monkeypatch.setattr(audio_normalize, "_decode_to_wav", lambda data, container: _wav(10))
    resp = _post("rec.webm", WEBM, "audio/webm", route="/v1/audio/translations")
    assert resp.status_code == 200
    assert upstream_calls[0]["kwargs"]["files"]["file"][2] == "audio/wav"


@pytest.mark.parametrize(
    "name,payload,content_type",
    [("a.wav", _wav(), "audio/wav"), ("a.mp4", MP4, "audio/mp4")],
)
def test_wav_and_mp4_are_forwarded_verbatim(monkeypatch, upstream_calls, name, payload, content_type):
    def boom(*_a, **_kw):
        raise AssertionError("a wav/mp4 upload must not reach the decoder")

    monkeypatch.setattr(audio_normalize, "_decode_to_wav", boom)

    resp = _post(name, payload, content_type)

    assert resp.status_code == 200
    sent = upstream_calls[0]["kwargs"]
    assert "files" not in sent
    assert payload in sent["content"]


def test_undecodable_upload_names_its_format(monkeypatch, upstream_calls):
    monkeypatch.setattr(audio_normalize.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        audio_normalize.subprocess,
        "run",
        lambda *_a, **_kw: subprocess.CompletedProcess(
            [], 1, stdout=b"", stderr=b"pipe:0: Invalid data found when processing input\n",
        ),
    )

    resp = _post("rec.webm", WEBM, "audio/webm")

    assert resp.status_code == 415
    detail = resp.json()["detail"]
    assert "webm" in detail and "Invalid data" in detail
    assert upstream_calls == []


def test_missing_ffmpeg_is_its_own_error(monkeypatch, upstream_calls):
    monkeypatch.setattr(audio_normalize.shutil, "which", lambda _name: None)

    resp = _post("rec.webm", WEBM, "audio/webm")

    assert resp.status_code == 503
    assert "ffmpeg" in resp.json()["detail"] and "webm" in resp.json()["detail"]
    assert upstream_calls == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("codec_args,suffix", [(["-c:a", "libopus"], "webm"), (["-c:a", "libopus"], "ogg")])
def test_real_clip_round_trips_to_16k_mono_wav(tmp_path, codec_args, suffix):
    clip = tmp_path / f"clip.{suffix}"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ar", "48000", "-ac", "2", *codec_args, str(clip)],
        check=True, timeout=60, creationflags=NO_WINDOW,
    )

    wav_bytes = audio_normalize._decode_to_wav(clip.read_bytes(), suffix)

    with wave.open(io.BytesIO(wav_bytes)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
        assert 15000 < w.getnframes() < 17500  # ~1 s: the header length is real, not unset
