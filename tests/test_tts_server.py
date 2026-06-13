"""TTS shim (src/tts_server) behaviour — engine fully mocked, no torch/weights.

The heavy synthesis engine is replaced with a fake so these run in CI where
chatterbox-tts / torch are absent. Covers: request validation, the
loading/ready 503 gate, and a happy-path synthesis returning audio bytes.
"""

from __future__ import annotations

import time

import yaml
from fastapi.testclient import TestClient

from src import host_profile, model_registry, tts_server
from src.tts_engines import SpeechRequest


def _config(tmp_path, monkeypatch):
    cfg = tmp_path / "models.yaml"
    cfg.write_text(yaml.safe_dump({
        "hub": {"port": 8000},
        "hosts": {"pc-cuda": {"platform": "win32", "default": True, "enabled": ["chatterbox"]}},
        "models": {
            "chatterbox": {
                "display_name": "chatterbox-tts",
                "aliases": ["audio_speech"],
                "backend": "tts",
                "engine": "tts-server",
                "tts_engine": "chatterbox",
                "port": 8092,
                "args": ["--device", "cpu"],
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(host_profile, "CONFIG_PATH", cfg)
    monkeypatch.setattr(model_registry, "CONFIG_PATH", cfg, raising=False)
    monkeypatch.setenv("LOCAL_LLM_HUB_HOST", "pc-cuda")


class _FakeEngine:
    def __init__(self) -> None:
        self.sample_rate = 24000
        self.last_req = None
        self._loaded = False

    def load(self) -> None:
        self._loaded = True

    def ready(self) -> bool:
        return self._loaded

    def synthesize(self, req: SpeechRequest):
        self.last_req = req
        return [0.0] * 240  # placeholder samples; encode_audio is mocked

    def synthesize_stream(self, req: SpeechRequest):
        self.last_req = req
        yield [0.0] * 120
        yield [0.0] * 120

    def close(self) -> None:
        self._loaded = False


def _client(tmp_path, monkeypatch, fake):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr(tts_server, "build_engine", lambda model, device: fake)
    # Avoid numpy/soundfile in CI: stub the encoder + streaming byte helpers
    # to fixed bytes.
    monkeypatch.setattr(
        tts_server, "encode_audio",
        lambda samples, sr, fmt: (b"RIFFfake-wav-bytes", "audio/wav"),
    )
    monkeypatch.setattr(tts_server, "_pcm16_bytes", lambda samples: b"\x01\x00")
    monkeypatch.setattr(
        tts_server, "_streaming_wav_header", lambda sr, **kw: b"RIFFstreamhdr"
    )
    app = tts_server.build_app("chatterbox", device="cpu")
    return TestClient(app)


def _wait_ready(client, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get("/health").json().get("ready"):
            return True
        time.sleep(0.02)
    return False


def test_missing_input_is_400(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, _FakeEngine()) as client:
        r = client.post("/v1/audio/speech", json={"model": "chatterbox-tts"})
        assert r.status_code == 400
        assert "input" in r.json()["detail"]


def test_speech_happy_path_returns_audio(tmp_path, monkeypatch):
    fake = _FakeEngine()
    with _client(tmp_path, monkeypatch, fake) as client:
        assert _wait_ready(client)
        r = client.post("/v1/audio/speech", json={
            "model": "chatterbox-tts",
            "input": "hello there",
            "voice": "default",
            "exaggeration": 0.8,
            "cfg_weight": 0.3,
            "response_format": "wav",
        })
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/")
        assert r.content == b"RIFFfake-wav-bytes"
        # The tone dial reached the engine.
        assert fake.last_req.exaggeration == 0.8
        assert fake.last_req.cfg_weight == 0.3
        assert fake.last_req.text == "hello there"


def test_health_reports_engine_and_readiness(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, _FakeEngine()) as client:
        assert _wait_ready(client)
        body = client.get("/health").json()
        assert body["ok"] is True
        assert body["engine"] == "chatterbox"
        assert body["ready"] is True


def test_encode_audio_wav_roundtrip(tmp_path, monkeypatch):
    """encode_audio produces a real WAV header for the default format."""
    import pytest

    np = pytest.importorskip("numpy")
    samples = np.zeros(240, dtype=np.float32)
    audio, media = tts_server.encode_audio(samples, 24000, "wav")
    assert media == "audio/wav"
    assert audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


def test_speech_streaming_returns_chunked_wav(tmp_path, monkeypatch):
    """stream_format=audio streams a WAV header followed by PCM16 frames."""
    fake = _FakeEngine()
    with _client(tmp_path, monkeypatch, fake) as client:
        assert _wait_ready(client)
        r = client.post("/v1/audio/speech", json={
            "model": "chatterbox-tts",
            "input": "hello there",
            "stream_format": "audio",
            "response_format": "wav",
        })
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/wav")
        assert r.headers["x-sample-rate"] == "24000"
        # Header emitted once, then one stubbed PCM frame per yielded chunk.
        assert r.content == b"RIFFstreamhdr" + b"\x01\x00" + b"\x01\x00"


def test_speech_streaming_pcm_is_headerless(tmp_path, monkeypatch):
    fake = _FakeEngine()
    with _client(tmp_path, monkeypatch, fake) as client:
        assert _wait_ready(client)
        r = client.post("/v1/audio/speech", json={
            "model": "chatterbox-tts",
            "input": "hello there",
            "stream_format": "audio",
            "response_format": "pcm",
        })
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/L16")
        assert r.content == b"\x01\x00" + b"\x01\x00"  # no WAV header


def test_speech_non_streaming_unchanged(tmp_path, monkeypatch):
    """Without stream_format the buffered single-Response path is unchanged."""
    fake = _FakeEngine()
    with _client(tmp_path, monkeypatch, fake) as client:
        assert _wait_ready(client)
        r = client.post("/v1/audio/speech", json={
            "model": "chatterbox-tts", "input": "hello there", "response_format": "wav",
        })
        assert r.status_code == 200
        assert r.content == b"RIFFfake-wav-bytes"


# ---- Orpheus incremental decode (no torch) ----

def _tok(code: int, pos: int) -> str:
    """Build the <custom_token_N> text that decodes to ``code`` at ``pos``."""
    from src.tts_engines import _SNAC_CODEBOOK

    return f"<custom_token_{code + 10 + (pos % 7) * _SNAC_CODEBOOK}>"


def test_iter_token_ids_reassembles_across_chunks():
    from src.tts_engines import OrpheusEngine

    full = _tok(5, 0) + _tok(6, 1) + _tok(7, 2)
    chunks = [full[:9], full[9:26], full[26:]]  # split mid-tag
    assert list(OrpheusEngine._iter_token_ids(chunks)) == [5, 6, 7]


def test_iter_token_ids_skips_control_without_shifting_frame():
    from src.tts_engines import OrpheusEngine

    # <custom_token_10> → tid 0 at pos 0: a control token, skipped without
    # advancing pos, so the next real token still decodes at pos 0.
    text = "<custom_token_10>" + _tok(5, 0)
    assert list(OrpheusEngine._iter_token_ids([text])) == [5]


def test_synthesize_stream_sliding_window_cadence(monkeypatch):
    import pytest
    from types import SimpleNamespace

    np = pytest.importorskip("numpy")
    from src.tts_engines import OrpheusEngine, SpeechRequest

    eng = OrpheusEngine(SimpleNamespace(internal_port=18093), device="cpu")
    monkeypatch.setattr(eng, "ready", lambda: True)
    # 35 tokens = 5 frames; each tid = 1 + frame index, always in range.
    text = "".join(_tok(1 + p // 7, p) for p in range(35))
    monkeypatch.setattr(eng, "_stream_completion", lambda prompt: [text])
    monkeypatch.setattr(eng, "_decode_window", lambda w: np.ones(2048, dtype=np.float32))

    out = list(eng.synthesize_stream(SpeechRequest(text="hi", voice="tara")))
    # Windows emit at len 28 and len 35 → two 2048-sample segments.
    assert len(out) == 2
    assert all(seg.shape == (2048,) for seg in out)


def test_synthesize_stream_short_input_falls_back_to_whole_clip(monkeypatch):
    import pytest
    from types import SimpleNamespace

    np = pytest.importorskip("numpy")
    from src.tts_engines import OrpheusEngine, SpeechRequest

    eng = OrpheusEngine(SimpleNamespace(internal_port=18093), device="cpu")
    monkeypatch.setattr(eng, "ready", lambda: True)
    text = "".join(_tok(1, p) for p in range(14))  # 2 frames, never reaches 28
    monkeypatch.setattr(eng, "_stream_completion", lambda prompt: [text])
    monkeypatch.setattr(eng, "_decode_snac", lambda codes: np.ones(4096, dtype=np.float32))

    out = list(eng.synthesize_stream(SpeechRequest(text="hi", voice="tara")))
    assert len(out) == 1 and out[0].shape == (4096,)


def test_default_synthesize_stream_yields_single_chunk():
    from src.tts_engines import SpeechRequest, TTSEngine

    class _Single(TTSEngine):
        def synthesize(self, req):
            return [0.1, 0.2, 0.3]

    out = list(_Single().synthesize_stream(SpeechRequest(text="x")))
    assert out == [[0.1, 0.2, 0.3]]
