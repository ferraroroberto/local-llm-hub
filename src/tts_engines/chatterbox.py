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

from .common import SpeechRequest, TTSEngine, _to_mono_f32, resolve_device, resolve_voice_clip

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
