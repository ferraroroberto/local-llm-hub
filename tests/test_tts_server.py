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

    def close(self) -> None:
        self._loaded = False


def _client(tmp_path, monkeypatch, fake):
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr(tts_server, "build_engine", lambda model, device: fake)
    # Avoid numpy/soundfile in CI: stub the encoder to fixed bytes.
    monkeypatch.setattr(
        tts_server, "encode_audio",
        lambda samples, sr, fmt: (b"RIFFfake-wav-bytes", "audio/wav"),
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
