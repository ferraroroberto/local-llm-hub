"""Chatterbox TTS engine — Resemble AI's Chatterbox loaded in-process via the
``chatterbox-tts`` package (torch). Carries an emotion/"tone" dial
(``exaggeration`` + ``cfg_weight``) and optional zero-shot voice cloning from
a reference clip dropped in ``config/tts_voices/``.

Heavy deps (torch, chatterbox-tts) are imported **lazily inside**
``load``/``synthesize`` so this module imports cleanly under pytest/CI where
they are absent. Install them with ``requirements-tts.txt`` on TTS-enabled
hosts (see ``scripts/install_tts.py``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from .common import (
    SpeechRequest,
    TTSEngine,
    TTS_LANGUAGE_LABELS,
    TTS_SAMPLE_TEXT,
    VOICES_DIR,
    _to_mono_f32,
    resolve_device,
    resolve_voice_clip,
    voice_option,
)

log = logging.getLogger(__name__)


class ChatterboxEngine(TTSEngine):
    """Resemble AI Chatterbox via the ``chatterbox-tts`` package."""

    def __init__(self, device: str = "auto") -> None:
        self.device_arg = device
        self.device = "cpu"
        self.model = None
        self.sample_rate = 24000

    def load(self) -> None:
        from chatterbox.tts import ChatterboxTTS  # heavy: torch

        self.device = resolve_device(self.device_arg)
        log.info("loading Chatterbox on %s …", self.device)
        self.model = ChatterboxTTS.from_pretrained(device=self.device)
        self.sample_rate = int(getattr(self.model, "sr", 24000))
        log.info("Chatterbox ready (sr=%d)", self.sample_rate)

    def ready(self) -> bool:
        return self.model is not None

    def validate_voice(self, voice: str) -> None:
        requested = (voice or "").strip()
        if not requested or requested.lower() in ("default", "none"):
            return
        if resolve_voice_clip(requested) is None:
            raise ValueError(f"unsupported Chatterbox voice clip: {requested}")

    def synthesize(self, req: SpeechRequest):
        if self.model is None:
            raise RuntimeError("Chatterbox not loaded")
        kwargs = {"exaggeration": req.exaggeration, "cfg_weight": req.cfg_weight}
        clip = resolve_voice_clip(req.voice)
        if clip is not None:
            kwargs["audio_prompt_path"] = str(clip)
        wav = self.model.generate(req.text, **kwargs)
        return _to_mono_f32(wav)

    def close(self) -> None:
        self.model = None

    @classmethod
    def capabilities(cls) -> Dict[str, Any]:
        voices = [voice_option("default", "Default", "en-US")]
        voices.extend(
            voice_option(path.stem, path.stem.replace("_", " ").title(), "en-US")
            for path in sorted(VOICES_DIR.glob("*.wav"))
        )
        return {
            "languages": [{"id": "en-US", "label": TTS_LANGUAGE_LABELS["en-US"]}],
            "voices": voices,
            "default_voice": "default",
            "default_language": "en-US",
            "sample_text": {"en-US": TTS_SAMPLE_TEXT["en-US"]},
            "controls": {"speed": False, "stream": False, "exaggeration": True, "cfg_weight": True},
        }
