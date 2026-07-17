"""Kokoro TTS engine — Kokoro-82M through ``kokoro-onnx`` / ONNX Runtime.

Tiny comparison option; loads a local ONNX model plus packed voice styles
from ``models/kokoro``. Heavy deps (kokoro_onnx, onnxruntime) are imported
**lazily inside** ``load`` so this module imports cleanly under pytest/CI
where they are absent.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

from ..model_registry import Model
from .common import (
    PROJECT_ROOT,
    SpeechRequest,
    TTSEngine,
    TTS_LANGUAGE_LABELS,
    TTS_SAMPLE_TEXT,
    _to_mono_f32,
    voice_option,
)

log = logging.getLogger(__name__)


class KokoroEngine(TTSEngine):
    """Kokoro-82M through kokoro-onnx / ONNX Runtime."""

    AVAILABLE_VOICES = [
        "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica",
        "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah",
        "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir",
        "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa",
        "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
        "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
        "ef_dora", "em_alex", "em_santa",
    ]
    # Calm male American voice; closest built-in Kokoro starting point for
    # a Jarvis-like assistant voice on this model family.
    DEFAULT_VOICE = "am_michael"

    @classmethod
    def capabilities(cls) -> Dict[str, Any]:
        language_by_prefix = {
            "af": "en-US", "am": "en-US", "bf": "en-GB",
            "bm": "en-GB", "ef": "es", "em": "es",
        }
        gender_by_prefix = {
            "af": "female", "am": "male", "bf": "female",
            "bm": "male", "ef": "female", "em": "male",
        }
        voices = []
        for voice in cls.AVAILABLE_VOICES:
            prefix, name = voice.split("_", 1)
            voices.append(voice_option(
                voice,
                name.replace("-", " ").title(),
                language_by_prefix[prefix],
                gender_by_prefix[prefix],
            ))
        languages = [
            {"id": language, "label": TTS_LANGUAGE_LABELS[language]}
            for language in ("en-US", "en-GB", "es")
        ]
        return {
            "languages": languages,
            "voices": voices,
            "default_voice": cls.DEFAULT_VOICE,
            "default_language": "en-US",
            "sample_text": {
                language: TTS_SAMPLE_TEXT[language]
                for language in ("en-US", "en-GB", "es")
            },
            "controls": {"speed": True, "stream": False, "exaggeration": False, "cfg_weight": False},
        }

    def __init__(self, model: Model, device: str = "auto") -> None:
        self.model_row = model
        self.device_arg = device
        self.device = "cpu"
        self.model = None
        self._dll_dirs: list = []
        self.sample_rate = 24000

    def _prepare_cuda_dll_path(self) -> None:
        """Make Torch's bundled CUDA/cuDNN DLLs visible to ONNX Runtime."""
        if sys.platform != "win32":
            return
        try:
            import torch

            torch_lib = Path(torch.__file__).resolve().parent / "lib"
        except Exception:  # noqa: BLE001
            return
        if not torch_lib.is_dir():
            return
        os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
        try:
            self._dll_dirs.append(os.add_dll_directory(str(torch_lib)))
        except (AttributeError, OSError):
            pass

    def load(self) -> None:
        from kokoro_onnx import Kokoro
        import onnxruntime as ort

        if not self.model_row.model_path:
            raise RuntimeError("kokoro row has no model_path (ONNX)")
        model_path = (PROJECT_ROOT / self.model_row.model_path).resolve()
        voices_path = model_path.parent / "voices-v1.0.bin"
        if not model_path.exists() or not voices_path.exists():
            raise RuntimeError(
                f"Kokoro model files not found at {model_path.parent} - "
                "run scripts/install_tts.py"
            )

        want = (self.device_arg or "auto").strip().lower()
        providers = set(ort.get_available_providers())
        use_cuda = want in ("cuda", "gpu") or (
            want == "auto" and "CUDAExecutionProvider" in providers
        )
        self.device = "cuda" if use_cuda else "cpu"
        previous_provider = os.environ.get("ONNX_PROVIDER")
        if use_cuda:
            # kokoro-onnx otherwise tries every available provider. On hosts
            # with TensorRT installed that can add startup cost or fail on
            # models we only need to run through plain CUDA.
            self._prepare_cuda_dll_path()
            os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
        else:
            os.environ.pop("ONNX_PROVIDER", None)
        log.info("loading Kokoro ONNX on %s from %s", self.device, model_path)
        try:
            self.model = Kokoro(str(model_path), str(voices_path))
        except Exception:
            if want != "auto" or not use_cuda:
                raise
            log.warning("Kokoro CUDA load failed; falling back to CPU", exc_info=True)
            os.environ.pop("ONNX_PROVIDER", None)
            self.device = "cpu"
            self.model = Kokoro(str(model_path), str(voices_path))
        finally:
            if previous_provider is not None:
                os.environ["ONNX_PROVIDER"] = previous_provider
        actual_providers = []
        try:
            actual_providers = list(self.model.sess.get_providers()) if self.model is not None else []
        except Exception:  # noqa: BLE001
            pass
        if "CUDAExecutionProvider" in actual_providers:
            self.device = "cuda"
        elif use_cuda and want != "auto":
            raise RuntimeError(
                f"Kokoro requested CUDA but ONNX Runtime used {actual_providers or 'no providers'}"
            )
        else:
            self.device = "cpu"
        self.sample_rate = 24000
        log.info("Kokoro ready (sr=%d, default_voice=%s)", self.sample_rate, self.DEFAULT_VOICE)

    def ready(self) -> bool:
        return self.model is not None

    @classmethod
    def _voice_for(cls, voice: str) -> str:
        v = (voice or "").strip()
        if not v or v.lower() in ("default", "none"):
            return cls.DEFAULT_VOICE
        if v not in cls.AVAILABLE_VOICES:
            raise ValueError(f"unsupported Kokoro voice: {v}")
        return v

    @staticmethod
    def _lang_for_voice(voice: str) -> str:
        if voice.startswith(("af_", "am_")):
            return "en-us"
        if voice.startswith(("bf_", "bm_")):
            return "en-gb"
        if voice.startswith(("ef_", "em_")):
            return "es"
        return "en-us"

    def validate_voice(self, voice: str) -> None:
        self._voice_for(voice)

    def synthesize(self, req: SpeechRequest):
        if self.model is None:
            raise RuntimeError("Kokoro not loaded")
        speed = max(0.5, min(2.0, float(req.speed or 1.0)))
        voice = self._voice_for(req.voice)
        audio, sample_rate = self.model.create(
            req.text,
            voice=voice,
            speed=speed,
            lang=self._lang_for_voice(voice),
        )
        self.sample_rate = int(sample_rate or 24000)
        return _to_mono_f32(audio)

    def close(self) -> None:
        self.model = None
