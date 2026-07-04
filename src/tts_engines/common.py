"""Shared TTS engine interface, device resolution, and audio helpers.

Every engine (:mod:`.chatterbox`, :mod:`.kokoro`, :mod:`.orpheus`,
:mod:`.piper`) implements :class:`TTSEngine`. Heavy deps (torch, numpy) are
still imported **lazily inside function bodies** here, same as the engine
modules, so this module imports cleanly under pytest/CI where they may be
absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VOICES_DIR = PROJECT_ROOT / "config" / "tts_voices"


def resolve_device(arg: Optional[str]) -> str:
    """Map ``--device`` (auto|cuda|cpu|mps) to a concrete torch device.

    ``auto`` prefers CUDA, then Apple MPS, else CPU. Never raises — falls
    back to ``cpu`` if torch can't be imported.
    """
    want = (arg or "auto").strip().lower()
    if want in ("cuda", "cpu", "mps"):
        return want
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def resolve_voice_clip(voice: str) -> Optional[Path]:
    """Resolve a ``voice`` name to a reference clip for cloning, or None.

    ``""``/``default`` → no clip (engine's built-in voice). A name maps to
    ``config/tts_voices/<voice>.wav``; an absolute/relative path that exists
    is used directly. Returns None when no clip is found (caller falls back
    to the default voice rather than erroring).
    """
    if not voice or voice.strip().lower() in ("default", "none"):
        return None
    p = Path(voice)
    if p.is_file():
        return p
    cand = VOICES_DIR / f"{voice}.wav"
    return cand if cand.is_file() else None


def _to_mono_f32(wav) -> "list":  # returns np.ndarray; annotated loosely (numpy lazy)
    """Coerce a torch tensor / numpy array of audio to 1-D float32 mono."""
    import numpy as np

    try:
        import torch

        if isinstance(wav, torch.Tensor):
            wav = wav.detach().cpu().float().numpy()
    except ImportError:
        pass
    arr = np.asarray(wav, dtype=np.float32)
    if arr.ndim > 1:
        # (1, N) → (N,); (C, N) with C>1 → average channels.
        arr = arr.reshape(-1) if arr.shape[0] == 1 else arr.mean(axis=0)
    return arr


def _wrap_on_words(segment: str, budget: int) -> List[str]:
    """Break ``segment`` into pieces of at most ``budget`` characters on word
    boundaries. A single word longer than ``budget`` is hard-sliced so a
    piece can never exceed the budget (last-resort; real text rarely hits it).
    """
    pieces: List[str] = []
    current = ""
    for word in segment.split():
        while len(word) > budget:  # pathological single word
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:budget])
            word = word[budget:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= budget:
            current += " " + word
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


@dataclass
class SpeechRequest:
    text: str
    voice: str = ""
    speed: float = 1.0
    # Chatterbox emotion/"tone" dial. Ignored by engines that lack it.
    exaggeration: float = 0.5
    cfg_weight: float = 0.5


class TTSEngine:
    """Common interface for a loaded text-to-speech engine."""

    sample_rate: int = 24000

    def load(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def ready(self) -> bool:  # pragma: no cover - overridden
        return False

    def synthesize(self, req: SpeechRequest):  # pragma: no cover - overridden
        raise NotImplementedError

    def synthesize_stream(self, req: SpeechRequest) -> Iterator:
        """Yield audio in chunks of mono float32 samples for incremental
        playback. Default: a single chunk wrapping :meth:`synthesize`, so
        engines that can't stream (Chatterbox) degrade gracefully to one
        final chunk.
        """
        yield self.synthesize(req)

    def close(self) -> None:  # pragma: no cover - overridden
        pass
