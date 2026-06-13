"""Install TTS deps and pre-warm the weights for enabled tts-server rows.

Mirrors ``install_whisper_cpp.py`` in spirit, but the TTS engines are
Python packages rather than a vendored binary:

  1. ``pip install -r requirements-tts.txt`` (Chatterbox's runtime deps +
     snac + soundfile — pulls torch), then ``chatterbox-tts`` itself with
     ``--no-deps``. Chatterbox's own dep set pins ``spacy-pkuseg`` (no
     Python-3.14 wheel → needs a C++ compiler) and ``gradio`` (a demo-UI
     framework); neither is needed for English server synthesis, so we
     install the package without letting pip pull them. Kept out of the base
     ``requirements.txt`` so non-TTS hosts stay lean.
  2. Pre-fetch the model weights so the first ``/v1/audio/speech`` request
     isn't a cold download:
       - Chatterbox + SNAC stream from the HF cache via ``from_pretrained``.
       - Orpheus is a GGUF — fetched into ``models/`` by ``download_models``.

Each step is best-effort: a failure is logged and the script continues, so a
flaky network fetch doesn't abort the whole install.

Usage:
    python scripts/install_tts.py
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.model_registry import enabled_models  # noqa: E402


# Installed --no-deps after the curated runtime set above — see module
# docstring (avoids spacy-pkuseg's compiler build + the gradio UI tree).
_CHATTERBOX_PIN = "chatterbox-tts==0.1.7"

# CUDA torch override. requirements-tts.txt pulls the CPU torch wheel from
# PyPI (the only Windows torch there), which runs synthesis at ~real-time on
# CPU — too slow. On an NVIDIA host we replace it with the CUDA build so the
# engines load on the GPU (~5x faster). The default index + pins are the pair
# validated on this repo's reference box (Python 3.14 / CUDA 13 / Blackwell:
# torch 2.11.0+cu130 — torchaudio has no 2.12 on cu130). Override per host via
# HUB_TTS_TORCH_INDEX / HUB_TTS_TORCH_SPEC if your Python/driver differ.
_CUDA_TORCH_INDEX = os.environ.get(
    "HUB_TTS_TORCH_INDEX", "https://download.pytorch.org/whl/cu130"
)
_CUDA_TORCH_SPEC = os.environ.get(
    "HUB_TTS_TORCH_SPEC", "torch==2.11.0+cu130 torchaudio==2.11.0+cu130"
).split()


def _pip_install_requirements() -> None:
    req = PROJECT_ROOT / "requirements-tts.txt"
    log.info("pip install -r %s", req)
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], check=True)
    log.info("pip install --no-deps %s", _CHATTERBOX_PIN)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", _CHATTERBOX_PIN],
        check=True,
    )


def _install_cuda_torch() -> None:
    """Replace the CPU torch with a CUDA build on NVIDIA hosts (best-effort).

    Skipped silently when no NVIDIA GPU is detected (``nvidia-smi`` absent) —
    those hosts keep the CPU torch from requirements. A failure here is
    logged but non-fatal: synthesis still works on CPU, just slowly.
    """
    if not shutil.which("nvidia-smi"):
        log.info("no NVIDIA GPU detected (nvidia-smi absent) — keeping CPU torch")
        return
    log.info("NVIDIA GPU detected — installing CUDA torch (%s)", " ".join(_CUDA_TORCH_SPEC))
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade",
             "--index-url", _CUDA_TORCH_INDEX, *_CUDA_TORCH_SPEC],
            check=True,
        )
        log.info("  CUDA torch installed")
    except subprocess.CalledProcessError as exc:
        log.warning(
            "CUDA torch install failed (%s) — falling back to CPU torch. "
            "Set HUB_TTS_TORCH_INDEX/HUB_TTS_TORCH_SPEC for your Python/driver.",
            exc,
        )


def _warm_chatterbox() -> None:
    try:
        from chatterbox.tts import ChatterboxTTS  # type: ignore

        log.info("pre-fetching Chatterbox weights (HF cache) …")
        ChatterboxTTS.from_pretrained(device="cpu")
        log.info("  Chatterbox weights ready")
    except Exception as exc:  # noqa: BLE001
        log.warning("Chatterbox warm-up skipped: %s", exc)


def _warm_snac() -> None:
    try:
        from snac import SNAC  # type: ignore

        log.info("pre-fetching SNAC codec weights (HF cache) …")
        SNAC.from_pretrained("hubertsiuzdak/snac_24khz")
        log.info("  SNAC weights ready")
    except Exception as exc:  # noqa: BLE001
        log.warning("SNAC warm-up skipped: %s", exc)


def _download_orpheus_gguf() -> None:
    orpheus = [m for m in enabled_models()
               if m.backend == "tts" and m.tts_engine == "orpheus" and m.hf_repo]
    if not orpheus:
        return
    try:
        from scripts import download_models  # type: ignore

        for m in orpheus:
            log.info("downloading Orpheus GGUF for %s …", m.id)
            download_models.download_one(m.id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Orpheus GGUF download skipped: %s — verify hf_repo/hf_pattern in models.yaml", exc)


def _tts_engines_enabled() -> set[str]:
    return {m.tts_engine for m in enabled_models()
            if m.backend == "tts" and m.tts_engine}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    engines = _tts_engines_enabled()
    if not engines:
        log.info("no tts-server row enabled on this host — nothing to install")
        return 0

    _pip_install_requirements()
    _install_cuda_torch()
    if "chatterbox" in engines:
        _warm_chatterbox()
    if "orpheus" in engines:
        _warm_snac()
        _download_orpheus_gguf()
    log.info("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
