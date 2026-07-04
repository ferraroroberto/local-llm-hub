"""TTS synthesis engines for the hub's ``/v1/audio/speech`` backend.

Engines behind one interface (:class:`TTSEngine` in :mod:`.common`),
selected per registry row's ``tts_engine`` field by :func:`build_engine`:

  - ``chatterbox`` (:mod:`.chatterbox`) — Resemble AI Chatterbox loaded
    in-process via the ``chatterbox-tts`` package (torch). Carries an
    emotion/"tone" dial (``exaggeration`` + ``cfg_weight``) and optional
    zero-shot voice cloning from a reference clip dropped in
    ``config/tts_voices/``.
  - ``orpheus`` (:mod:`.orpheus`) — Orpheus-3B run as a GGUF on a loopback
    ``llama-server`` child (reusing the vendored binary) whose emitted audio
    tokens are decoded with the SNAC neural codec in-process. The most
    expressive option, but heavier. Orpheus's reference runtime (vLLM) has
    no usable Windows build, hence the llama.cpp + SNAC route.
  - ``kokoro`` (:mod:`.kokoro`) — Kokoro-82M via ONNX Runtime. Tiny
    comparison option; loads a local ONNX model plus packed voice styles
    from ``models/kokoro``.
  - ``piper`` (:mod:`.piper`) — Piper VITS voices through the standalone
    Piper binary. Fast CPU path for short assistant replies; voices live in
    ``models/piper``.

``process.py`` holds the shared Windows job-object process-lifecycle
helpers (kill-on-close job, no-window subprocess flags) used by Piper's
resident process pool and Orpheus's llama-server child. ``common.py`` holds
the shared :class:`TTSEngine` interface, ``SpeechRequest``, device
resolution, and audio helpers.

Heavy deps (torch, chatterbox-tts, snac, kokoro-onnx, soundfile) are imported
**lazily inside each engine's** ``load``/``synthesize`` so this package
imports cleanly under pytest/CI where they are absent. Install them with
``requirements-tts.txt`` on TTS-enabled hosts (see ``scripts/install_tts.py``).
"""

from __future__ import annotations

from ..model_registry import Model
from .chatterbox import ChatterboxEngine
from .common import PROJECT_ROOT, SpeechRequest, TTSEngine, VOICES_DIR, resolve_device, resolve_voice_clip
from .kokoro import KokoroEngine
from .orpheus import (
    OrpheusEngine,
    _MAX_CHARS_PER_CHUNK,
    _N_PREDICT,
    _ORPHEUS_TOKEN_RE,
    _SNAC_CODEBOOK,
)
from .piper import PiperEngine, _PiperProc

__all__ = [
    "TTSEngine",
    "SpeechRequest",
    "ChatterboxEngine",
    "KokoroEngine",
    "OrpheusEngine",
    "PiperEngine",
    "build_engine",
    "resolve_device",
    "resolve_voice_clip",
    "PROJECT_ROOT",
    "VOICES_DIR",
]


def build_engine(model: Model, device: str = "auto") -> TTSEngine:
    """Construct (not load) the engine named by ``model.tts_engine``."""
    eng = (model.tts_engine or "").strip().lower()
    if eng == "chatterbox":
        return ChatterboxEngine(device)
    if eng == "kokoro":
        return KokoroEngine(model, device)
    if eng == "orpheus":
        return OrpheusEngine(model, device)
    if eng == "piper":
        return PiperEngine(model, device)
    raise ValueError(
        f"unknown tts_engine {model.tts_engine!r} for model {model.id!r} "
        f"(expected 'chatterbox', 'kokoro', 'orpheus', or 'piper')"
    )
