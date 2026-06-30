"""TTS synthesis engines for the hub's ``/v1/audio/speech`` backend.

Engines behind one interface, selected per registry row's
``tts_engine`` field by :func:`build_engine`:

  - ``chatterbox`` — Resemble AI Chatterbox loaded in-process via the
    ``chatterbox-tts`` package (torch). Carries an emotion/"tone" dial
    (``exaggeration`` + ``cfg_weight``) and optional zero-shot voice
    cloning from a reference clip dropped in ``config/tts_voices/``.
  - ``orpheus`` — Orpheus-3B run as a GGUF on a loopback ``llama-server``
    child (reusing the vendored binary) whose emitted audio tokens are
    decoded with the SNAC neural codec in-process. The most expressive
    option, but heavier. Orpheus's reference runtime (vLLM) has no usable
    Windows build, hence the llama.cpp + SNAC route.
  - ``kokoro`` — Kokoro-82M via ONNX Runtime. Tiny comparison option;
    loads a local ONNX model plus packed voice styles from ``models/kokoro``.
  - ``piper`` — Piper VITS voices through the standalone Piper binary. Fast
    CPU path for short assistant replies; voices live in ``models/piper``.

Heavy deps (torch, chatterbox-tts, snac, soundfile) are imported **lazily
inside ``load``/``synthesize``** so this module imports cleanly under
pytest/CI where they are absent. Install them with ``requirements-tts.txt``
on TTS-enabled hosts (see ``scripts/install_tts.py``).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

import httpx

from .model_registry import Model

log = logging.getLogger("tts_engines")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VOICES_DIR = PROJECT_ROOT / "config" / "tts_voices"

# SNAC code book size — each audio token, after offset removal, lands in
# [0, 4096). Frames are 7 tokens that fan out into SNAC's 3 hierarchical
# layers (1 / 2 / 4 codes). Orpheus token ids carry a +10 base offset and a
# per-position +4096*(i%7) stride (canopyai/Orpheus convention).
_SNAC_CODEBOOK = 4096
_ORPHEUS_TOKEN_RE = re.compile(r"<custom_token_(\d+)>")

# A single llama-server ``/completion`` is capped at ``_N_PREDICT`` generated
# audio tokens. 4096 SNAC tokens ≈ 49.6 s of speech (4096 ÷ 7 codes/frame ×
# 2048 samples/frame ÷ 24 kHz). Synthesising longer than that in one request
# silently truncates the audio (issue #130), so long input is split into
# chunks that each comfortably fit under the cap and then concatenated.
#
# Orpheus emits roughly 18 characters of text per second of speech, so the
# ~49.6 s ceiling is ~900 characters. ``_MAX_CHARS_PER_CHUNK`` budgets each
# chunk at ~27 s — generous headroom against rate variance, and small enough
# that single generations stay coherent (very long ones also degrade quality).
_N_PREDICT = 4096
_MAX_CHARS_PER_CHUNK = 480
# Split on whitespace that follows sentence-ending punctuation, keeping the
# punctuation with its sentence.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _win_kill_on_close_job():
    """Create a Windows Job Object that kills every assigned process when
    its last handle closes — i.e. when *this* (parent) process dies, by
    crash, ``terminate()``, or clean exit.

    The hub stops a backend with ``TerminateProcess`` (no atexit/finally),
    so a llama-server grandchild spawned in its own process group would
    otherwise leak — holding GPU VRAM and its internal port. Assigning it
    to this job makes the OS reap it whenever we go away. Returns the job
    handle (the caller must keep it alive) or ``None`` on non-Windows /
    failure (callers fall back to the explicit ``terminate`` in ``close``).
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    class _BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _EXTENDED(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC),
            ("IoInfo", _IO),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _EXTENDED()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ):
        k32.CloseHandle(job)
        return None
    return job


def _assign_to_job(job, proc: "subprocess.Popen") -> bool:
    """Assign ``proc`` to a Windows job handle from :func:`_win_kill_on_close_job`."""
    if job is None or sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    return bool(k32.AssignProcessToJobObject(job, int(proc._handle)))


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


class KokoroEngine(TTSEngine):
    """Kokoro-82M through kokoro-onnx / ONNX Runtime."""

    AVAILABLE_VOICES = [
        "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica",
        "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah",
        "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir",
        "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa",
        "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
        "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    ]
    # Calm male American voice; closest built-in Kokoro starting point for
    # a Jarvis-like assistant voice on this model family.
    DEFAULT_VOICE = "am_michael"

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
        return v if v in cls.AVAILABLE_VOICES else cls.DEFAULT_VOICE

    @staticmethod
    def _lang_for_voice(voice: str) -> str:
        if voice.startswith(("af_", "am_")):
            return "en-us"
        if voice.startswith(("bf_", "bm_")):
            return "en-gb"
        return "en-us"

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


class _PiperProc:
    """One resident ``piper.exe`` for a fixed (voice model, length_scale).

    The whole point of resident mode: the ONNX voice is loaded **once** at
    spawn and reused across requests, instead of paying a fresh model load on
    every call (measured ~0.40 s → ~0.07 s per short utterance — the inference
    itself is ~0.06 s; the rest was startup tax). Utterances are fed as
    ``--json-input`` lines; piper prints each finished WAV's path on stdout
    (emitted even under ``--quiet``) as the per-utterance completion signal.

    A single piper process synthesises one utterance at a time, so
    :meth:`synth` serialises access with a lock — concurrent hub requests for
    the *same* (voice, speed) queue here, while a different combination runs on
    its own resident process. Death is detected via a sentinel pushed by the
    reader thread, so a crashed child is recycled on the next call.
    """

    def __init__(self, cmd: List[str], out_dir: Path, job) -> None:
        self._cmd = cmd
        self._out_dir = out_dir
        self._lock = threading.Lock()
        self._done: "queue.Queue[Optional[str]]" = queue.Queue()
        self._counter = 0
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(PROJECT_ROOT),
        )
        _assign_to_job(job, self._proc)
        threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self._done.put(line)
        self._done.put(None)  # EOF — the child exited

    def alive(self) -> bool:
        return self._proc.poll() is None

    def synth(self, text: str, timeout: float = 60.0) -> bytes:
        """Synthesise one utterance and return its WAV bytes. Raises on any
        failure (dead child, broken pipe, timeout) so the caller can fall back
        to a one-shot subprocess and recycle this process."""
        with self._lock:
            if self._proc.poll() is not None:
                raise RuntimeError("piper resident process is not running")
            # Drain any stale completion lines (shouldn't happen under the lock,
            # but keeps one synth strictly paired with one stdout line).
            try:
                while True:
                    self._done.get_nowait()
            except queue.Empty:
                pass
            self._counter += 1
            out_path = self._out_dir / f"u{self._counter}.wav"
            line = json.dumps({"text": text.strip(), "output_file": str(out_path)}) + "\n"
            assert self._proc.stdin is not None
            try:
                self._proc.stdin.write(line.encode("utf-8"))
                self._proc.stdin.flush()
            except OSError as exc:
                raise RuntimeError(f"piper resident stdin write failed: {exc}")
            signal_line = self._done.get(timeout=timeout)
            if signal_line is None:
                raise RuntimeError("piper resident process exited mid-synthesis")
            try:
                return out_path.read_bytes()
            finally:
                try:
                    out_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def close(self) -> None:
        proc = self._proc
        if proc.poll() is not None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass


class PiperEngine(TTSEngine):
    """Piper standalone binary with local ONNX voices.

    Keeps a pool of **resident** ``piper.exe`` processes (one per (voice,
    speed) combination, lazily spawned) so the ONNX voice is loaded once and
    reused, instead of spawning — and re-loading — a fresh binary per request.
    A resident process whose synthesis fails for any reason falls back to a
    single one-shot subprocess for that request and is recycled.
    """

    DEFAULT_VOICE = "ryan"
    VOICE_FILES = {
        "default": "en_US-ryan-medium.onnx",
        "ryan": "en_US-ryan-medium.onnx",
        "ryan-medium": "en_US-ryan-medium.onnx",
        "en_us-ryan-medium": "en_US-ryan-medium.onnx",
        "en_us_ryan_medium": "en_US-ryan-medium.onnx",
        "ryan-high": "en_US-ryan-high.onnx",
        "en_us-ryan-high": "en_US-ryan-high.onnx",
        "en_us_ryan_high": "en_US-ryan-high.onnx",
        "lessac": "en_US-lessac-medium.onnx",
        "lessac-medium": "en_US-lessac-medium.onnx",
        "en_us-lessac-medium": "en_US-lessac-medium.onnx",
        "en_us_lessac_medium": "en_US-lessac-medium.onnx",
    }

    def __init__(self, model: Model, device: str = "auto") -> None:
        self.model_row = model
        self.device_arg = device
        self.device = "cpu"
        self.binary = self._default_binary()
        self.default_model: Optional[Path] = None
        self.sample_rate = 22050
        # Resident piper.exe pool, keyed by (model_path_str, length_scale).
        self._procs: dict = {}
        self._procs_lock = threading.Lock()
        self._out_dir: Optional[Path] = None
        self._job = None  # Windows kill-on-close job for the resident children

    @staticmethod
    def _default_binary() -> Path:
        name = "piper.exe" if sys.platform == "win32" else "piper"
        return PROJECT_ROOT / "vendor" / "piper" / name

    @staticmethod
    def _config_path(model_path: Path) -> Path:
        return Path(str(model_path) + ".json")

    @staticmethod
    def _read_sample_rate(config_path: Path) -> int:
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            return int(data.get("audio", {}).get("sample_rate") or 22050)
        except Exception:  # noqa: BLE001
            return 22050

    def load(self) -> None:
        if not self.model_row.model_path:
            raise RuntimeError("piper row has no model_path (ONNX voice)")
        model_path = (PROJECT_ROOT / self.model_row.model_path).resolve()
        config_path = self._config_path(model_path)
        if not self.binary.exists():
            raise RuntimeError(f"Piper binary not found at {self.binary} - run scripts/install_tts.py")
        if not model_path.exists() or not config_path.exists():
            raise RuntimeError(
                f"Piper voice files not found at {model_path.parent} - run scripts/install_tts.py"
            )
        self.default_model = model_path
        self.sample_rate = self._read_sample_rate(config_path)
        self.device = "cpu"
        self._out_dir = Path(tempfile.mkdtemp(prefix="piper-resident-"))
        # Tie resident children to a kill-on-close job so a TerminateProcess on
        # this backend (the hub's stop path) can't leak piper.exe processes.
        self._job = _win_kill_on_close_job()
        log.info(
            "Piper ready (resident mode, binary=%s, voice=%s, sr=%d)",
            self.binary,
            model_path.name,
            self.sample_rate,
        )
        # Pre-warm the default voice so the first real request is already warm
        # (the cold model load happens here, in the background load thread,
        # rather than on the first user-facing synthesis).
        try:
            self.synthesize(SpeechRequest(text="warming up", voice=self.DEFAULT_VOICE))
        except Exception as exc:  # noqa: BLE001
            log.warning("Piper pre-warm failed (non-fatal): %s", exc)

    def ready(self) -> bool:
        return self.default_model is not None

    def _model_for_voice(self, voice: str) -> Path:
        assert self.default_model is not None
        raw = (voice or "").strip()
        key = raw.lower().replace(" ", "-")
        if not key or key == "none":
            return self.default_model
        direct = Path(raw)
        if direct.is_file():
            return direct.resolve()
        filename = self.VOICE_FILES.get(key)
        if filename:
            candidate = self.default_model.parent / filename
            if candidate.exists():
                return candidate
        candidate = self.default_model.parent / f"{raw}.onnx"
        if candidate.exists():
            return candidate
        log.info("unknown Piper voice %r; using %s", raw, self.default_model.name)
        return self.default_model

    def _resident_cmd(self, model_path: Path, config_path: Path, length_scale: float) -> List[str]:
        assert self._out_dir is not None
        espeak_dir = self.binary.parent / "espeak-ng-data"
        cmd = [
            str(self.binary),
            "--model", str(model_path),
            "--config", str(config_path),
            "--length_scale", f"{length_scale:.4f}",
            "--sentence_silence", "0.05",
            "--quiet",
            "--json-input",
            "--output_dir", str(self._out_dir),
        ]
        if espeak_dir.is_dir():
            cmd.extend(["--espeak_data", str(espeak_dir)])
        return cmd

    def _get_proc(self, model_path: Path, config_path: Path, length_scale: float) -> _PiperProc:
        # length_scale (not voice+speed) keys speed; rounded so float jitter
        # doesn't fragment the pool. The common case (default voice, 1.0) maps
        # to a single warm process.
        key = (str(model_path), round(length_scale, 4))
        with self._procs_lock:
            proc = self._procs.get(key)
            if proc is not None and proc.alive():
                return proc
            if proc is not None:  # dead — recycle
                proc.close()
            cmd = self._resident_cmd(model_path, config_path, length_scale)
            proc = _PiperProc(cmd, self._out_dir, self._job)
            self._procs[key] = proc
            return proc

    def _wav_bytes_to_samples(self, data: bytes):
        import io

        import numpy as np

        if not data or len(data) <= 44:
            return np.zeros(0, dtype=np.float32)
        with wave.open(io.BytesIO(data), "rb") as wav:
            self.sample_rate = int(wav.getframerate())
            channels = int(wav.getnchannels())
            frames = wav.readframes(wav.getnframes())
        pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32)
        if channels > 1:
            pcm = pcm.reshape(-1, channels).mean(axis=1)
        return pcm / 32768.0

    def synthesize(self, req: SpeechRequest):
        if self.default_model is None:
            raise RuntimeError("Piper not loaded")
        model_path = self._model_for_voice(req.voice)
        config_path = self._config_path(model_path)
        self.sample_rate = self._read_sample_rate(config_path)
        # Piper's length_scale is inverse speed: lower scale speaks faster.
        speed = max(0.5, min(2.0, float(req.speed or 1.0)))
        length_scale = 1.0 / speed
        try:
            proc = self._get_proc(model_path, config_path, length_scale)
            return self._wav_bytes_to_samples(proc.synth(req.text))
        except Exception as exc:  # noqa: BLE001
            # Resident path wedged or the child died — never fail the request:
            # drop that process and synthesise this one with a fresh one-shot.
            log.warning("resident Piper synth failed (%s); falling back to one-shot", exc)
            with self._procs_lock:
                stale = self._procs.pop((str(model_path), round(length_scale, 4)), None)
            if stale is not None:
                stale.close()
            return self._synthesize_oneshot(model_path, config_path, length_scale, req.text)

    def _synthesize_oneshot(self, model_path: Path, config_path: Path, length_scale: float, text: str):
        """Fallback: a single fresh piper.exe per call (the pre-resident path).
        Used only when the resident process fails, so synthesis is resilient."""
        import numpy as np

        espeak_dir = self.binary.parent / "espeak-ng-data"
        cmd = [
            str(self.binary),
            "--model", str(model_path),
            "--config", str(config_path),
            "--length_scale", f"{length_scale:.4f}",
            "--sentence_silence", "0.05",
            "--quiet",
        ]
        if espeak_dir.is_dir():
            cmd.extend(["--espeak_data", str(espeak_dir)])
        with tempfile.NamedTemporaryFile(prefix="piper-", suffix=".wav", delete=False) as tmp:
            out_path = Path(tmp.name)
        try:
            proc = subprocess.run(
                [*cmd, "--output_file", str(out_path)],
                input=(text.strip() + "\n").encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(PROJECT_ROOT),
                timeout=60,
                check=False,
            )
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"Piper exited {proc.returncode}: {err}")
            if not out_path.exists():
                return np.zeros(0, dtype=np.float32)
            return self._wav_bytes_to_samples(out_path.read_bytes())
        finally:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

    def close(self) -> None:
        with self._procs_lock:
            for proc in self._procs.values():
                proc.close()
            self._procs.clear()
        if self._job is not None and sys.platform == "win32":
            try:
                import ctypes

                ctypes.WinDLL("kernel32").CloseHandle(int(self._job))
            except Exception:  # noqa: BLE001
                pass
            self._job = None
        if self._out_dir is not None:
            import shutil

            shutil.rmtree(self._out_dir, ignore_errors=True)
            self._out_dir = None


class OrpheusEngine(TTSEngine):
    """Orpheus-3B: GGUF on a loopback llama-server child + SNAC decode."""

    AVAILABLE_VOICES = ["tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"]
    DEFAULT_VOICE = "tara"
    LLAMA_READY_DEADLINE_S = 180.0  # 3B cold-load on first start

    def __init__(self, model: Model, device: str = "auto") -> None:
        self.model_row = model
        self.device_arg = device
        self.device = "cpu"
        self.snac = None
        self.proc: Optional[subprocess.Popen] = None
        self._job = None  # Windows Job handle: kills the llama child when we die
        self.internal_port = int(model.internal_port or 18093)
        self.sample_rate = 24000
        # Persistent client for the loopback /completion calls to our own
        # llama-server child. Constructing an httpx client is ~0.26s on Windows
        # (#165), so a per-request client would tax every synthesis; one
        # reused client costs ~1ms. This engine runs in the tts_server process,
        # not the hub, so it can't use the hub's shared client.
        self._client: Optional[httpx.Client] = None

    # ---- lifecycle ----

    def load(self) -> None:
        import torch  # noqa: F401  (presence check; SNAC needs it)
        from snac import SNAC

        self.device = resolve_device(self.device_arg)
        log.info("loading SNAC codec on %s …", self.device)
        self.snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval().to(self.device)
        self._client = httpx.Client(timeout=300.0)
        self._spawn_llama()
        self._wait_llama_ready()
        log.info("Orpheus ready (llama-server :%d, SNAC on %s)", self.internal_port, self.device)

    def _reclaim_internal_port(self) -> None:
        """Kill any stale listener on our internal port before spawning.

        Defends against a llama-server orphaned by a previous session that
        died before the job object could reap it — without this, the fresh
        spawn can't bind ``internal_port``. The port is hub-private, so
        killing whatever holds it is safe.
        """
        from .server_process import kill_pid, snapshot_listening_pids

        for pid in snapshot_listening_pids().get(self.internal_port, []) or []:
            log.warning("reclaiming stale process %s on internal port %s", pid, self.internal_port)
            kill_pid(int(pid))

    def _spawn_llama(self) -> None:
        from .backend_process import llama_server_binary, VENDOR_LLAMA
        from .server_process import WIN_NEW_GROUP

        self._reclaim_internal_port()
        bin_path = llama_server_binary()
        if not bin_path.exists():
            raise RuntimeError(
                f"llama-server not found at {bin_path} - run scripts/install_llama_cpp.py"
            )
        if not self.model_row.model_path:
            raise RuntimeError("orpheus row has no model_path (GGUF)")
        gguf = (PROJECT_ROOT / self.model_row.model_path).resolve()
        if not gguf.exists():
            raise RuntimeError(
                f"Orpheus GGUF not found at {gguf} - "
                f"run scripts/download_models.py --only {self.model_row.id}"
            )
        # Throughput note (issue #105): on the reference GPU this 3B Q4_K_M
        # generates ~150 tok/s, which sets the total synthesis time. That rate
        # is memory-bandwidth bound, NOT a missing flag — the model is fully
        # offloaded (-ngl 99) and llama.cpp already auto-enables flash
        # attention, so --flash-attn / -b/-ub batch / --no-mmap leave the rate
        # unchanged (measured: scripts/bench_orpheus.py). ~150 tok/s is ~65% of
        # this card's bandwidth ceiling for a ~1.94 GB resident model; the only
        # faster route is a lower quant, which would regress SNAC audio quality.
        # Perceived latency is handled by streaming (#102). Full analysis +
        # before/after numbers: docs/orpheus-throughput.md.
        #
        # -c 8192 (not smaller): KV is sized by n_ctx, not per slot, so a
        # shorter context would not free meaningful VRAM, and 8192 is needed to
        # hold longer inputs (Orpheus emits ~107 audio tokens per second of
        # speech, so 8192 ≈ 76 s of audio headroom).
        cmd = [
            str(bin_path),
            "-m", str(gguf),
            "--host", "127.0.0.1",
            "--port", str(self.internal_port),
            "-c", "8192",
            "-ngl", "99",
            "--no-webui",
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        if sys.platform == "win32":
            env["PATH"] = str(VENDOR_LLAMA) + os.pathsep + env.get("PATH", "")
        log.info("spawning Orpheus llama-server: %s", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=WIN_NEW_GROUP,
        )
        # Tie the child's lifetime to ours: when this tts_server process
        # dies (even via the hub's TerminateProcess), the OS closes the job
        # handle and reaps the llama-server, freeing its VRAM and port.
        self._job = _win_kill_on_close_job()
        if not _assign_to_job(self._job, self.proc) and self._job is not None:
            log.warning("could not assign llama-server to job object; relying on close()")
        t = threading.Thread(target=self._forward_stdout, args=(self.proc,), daemon=True)
        t.start()

    def _forward_stdout(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            sys.stdout.write(f"[orpheus-llama] {raw.rstrip()}\n")
            sys.stdout.flush()

    def _wait_llama_ready(self) -> None:
        deadline = time.monotonic() + self.LLAMA_READY_DEADLINE_S
        url = f"http://127.0.0.1:{self.internal_port}/health"
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError("Orpheus llama-server child exited during startup")
            try:
                r = httpx.get(url, timeout=2.0)
                if r.status_code == 200:
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        self.close()
        raise RuntimeError(
            f"Orpheus llama-server did not become ready within {self.LLAMA_READY_DEADLINE_S:.0f}s"
        )

    def ready(self) -> bool:
        return self.snac is not None and self.proc is not None and self.proc.poll() is None

    # ---- synthesis ----

    def _prompt_for(self, req: SpeechRequest, text: Optional[str] = None) -> str:
        voice = req.voice if req.voice in self.AVAILABLE_VOICES else self.DEFAULT_VOICE
        body = req.text if text is None else text
        # Orpheus-FastAPI prompt convention for the llama.cpp route: the
        # end marker is the model's <|eot_id|> special token (not <|eot|>).
        return f"<|audio|>{voice}: {body}<|eot_id|>"

    @staticmethod
    def _split_into_chunks(text: str, budget: int = _MAX_CHARS_PER_CHUNK) -> List[str]:
        """Split ``text`` into chunks no longer than ``budget`` characters.

        Input that already fits in a single chunk is returned **unchanged**
        (``[text]``) so short synthesis is byte-for-byte identical to the
        pre-chunking behaviour. Longer input is broken on sentence
        boundaries and greedily packed; a single sentence over budget is
        hard-wrapped on word boundaries so no chunk can exceed ``budget``.
        """
        if not text or not text.strip():
            return []
        if len(text) <= budget:
            return [text]
        chunks: List[str] = []
        current = ""
        for sentence in (s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip())):
            if not sentence:
                continue
            pieces = [sentence] if len(sentence) <= budget else _wrap_on_words(sentence, budget)
            for piece in pieces:
                if not current:
                    current = piece
                elif len(current) + 1 + len(piece) <= budget:
                    current += " " + piece
                else:
                    chunks.append(current)
                    current = piece
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _completion_payload(prompt: str, stream: bool) -> dict:
        return {
            "prompt": prompt,
            "n_predict": _N_PREDICT,
            "temperature": 0.6,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "cache_prompt": True,
            "stream": stream,
        }

    def synthesize(self, req: SpeechRequest):
        import numpy as np

        if not self.ready():
            raise RuntimeError("Orpheus not loaded")
        # Long input is split into per-chunk /completion calls (each under the
        # n_predict cap) and the decoded PCM segments concatenated in order;
        # short input is a single chunk, so this is identical to before (#130).
        segments = [self._synthesize_chunk(req, chunk) for chunk in self._split_into_chunks(req.text)]
        segments = [seg for seg in segments if seg.size]
        if not segments:
            log.warning("Orpheus emitted no audio tokens for input")
            return np.zeros(0, dtype=np.float32)
        return segments[0] if len(segments) == 1 else np.concatenate(segments)

    def _synthesize_chunk(self, req: SpeechRequest, chunk: str):
        """Synthesise one text chunk through a single buffered /completion."""
        import numpy as np

        prompt = self._prompt_for(req, chunk)
        url = f"http://127.0.0.1:{self.internal_port}/completion"
        assert self._client is not None
        r = self._client.post(
            url, json=self._completion_payload(prompt, stream=False), timeout=300.0
        )
        r.raise_for_status()
        content = r.json().get("content", "")
        codes = self._parse_tokens(content)
        if not codes:
            return np.zeros(0, dtype=np.float32)
        return self._decode_snac(codes)

    # ---- streaming synthesis ----

    def synthesize_stream(self, req: SpeechRequest):
        """Decode SNAC frames incrementally as the llama-server streams them.

        Mirrors the canopyai/Orpheus-FastAPI ``speechpipe`` sliding window:
        on every completed 7-token frame past a 4-frame warmup, decode the
        newest 28-token window and emit its artefact-free ``[2048:4096]``
        segment (~85 ms at 24 kHz). Short inputs that never reach the window
        fall back to a single whole-clip decode so they still produce audio.
        """
        if not self.ready():
            raise RuntimeError("Orpheus not loaded")
        # Stream chunk after chunk back-to-back: long input is split so each
        # chunk stays under the n_predict cap (#130), while a short single
        # chunk streams exactly as before — time-to-first-audio is unchanged.
        for chunk in self._split_into_chunks(req.text):
            yield from self._stream_chunk(req, chunk)

    def _stream_chunk(self, req: SpeechRequest, chunk: str):
        """Stream one text chunk's PCM via the sliding-window SNAC decode."""
        prompt = self._prompt_for(req, chunk)
        buffer: List[int] = []
        emitted = False
        for tid in self._iter_token_ids(self._stream_completion(prompt)):
            buffer.append(tid)
            if len(buffer) % 7 == 0 and len(buffer) >= 28:
                seg = self._decode_window(buffer[-28:])
                if seg.size:
                    emitted = True
                    yield seg
        if not emitted and buffer:
            audio = self._decode_snac(buffer)
            if audio.size:
                yield audio

    def _stream_completion(self, prompt: str) -> Iterator[str]:
        """Stream the llama-server ``/completion`` SSE, yielding each delta's
        ``content`` text as it arrives."""
        url = f"http://127.0.0.1:{self.internal_port}/completion"
        assert self._client is not None
        with self._client.stream(
            "POST", url, json=self._completion_payload(prompt, stream=True), timeout=300.0
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                if line.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    continue
                piece = obj.get("content", "")
                if piece:
                    yield piece
                if obj.get("stop"):
                    break

    @staticmethod
    def _iter_token_ids(text_chunks: Iterable[str]) -> Iterator[int]:
        """Stream SNAC code ids from a sequence of completion text deltas.

        Buffers a partial ``<custom_token_N>`` tail across chunk boundaries
        (a token may be split mid-tag), applies the +10 / per-position
        stride offset, and skips control tokens (id <= 0) **without
        advancing the frame position** — the same validated semantics as
        :meth:`_parse_tokens`, made incremental.
        """
        carry = ""
        pos = 0
        for chunk in text_chunks:
            carry += chunk
            last_end = 0
            for m in _ORPHEUS_TOKEN_RE.finditer(carry):
                tid = int(m.group(1)) - 10 - ((pos % 7) * _SNAC_CODEBOOK)
                if tid > 0:
                    yield tid
                    pos += 1
                last_end = m.end()
            # Retain the unconsumed tail (a possible partial token) for the
            # next chunk; a closing '>' only appears at a token's true end,
            # so splitting mid-number can never match prematurely.
            carry = carry[last_end:]

    def _decode_window(self, window: List[int]):
        """Decode a 28-token (4-frame) sliding window and return its newest
        2048-sample segment. Empty array if any code is out of range."""
        import numpy as np

        frames = [window[7 * j: 7 * j + 7] for j in range(len(window) // 7)]
        if not frames or any(not all(0 <= c < _SNAC_CODEBOOK for c in f) for f in frames):
            return np.zeros(0, dtype=np.float32)
        return self._snac_decode(frames)[2048:4096]

    @staticmethod
    def _parse_tokens(text: str) -> List[int]:
        """Parse ``<custom_token_N>`` strings into SNAC code ids.

        Removes the +10 base offset and the per-position +4096*(pos%7)
        stride so each returned id lands in [0, 4096). The model prefixes
        the audio stream with a few small control tokens (e.g. 4, 5, 1);
        those decode to a non-positive id, so — matching the canopyai /
        Orpheus-FastAPI ``speechpipe`` decoder — they are skipped *without*
        advancing the frame position. That re-aligns the 7-token framing to
        the first real audio token; indexing every token instead would shift
        every frame and make all codes fall out of range (silent output).
        """
        ids: List[int] = []
        pos = 0
        for m in _ORPHEUS_TOKEN_RE.finditer(text):
            tid = int(m.group(1)) - 10 - ((pos % 7) * _SNAC_CODEBOOK)
            if tid > 0:
                ids.append(tid)
                pos += 1
        return ids

    def _decode_snac(self, code_list: List[int]):
        import numpy as np

        # Whole 7-token frames only; drop any frame with an out-of-range code.
        n_frames = len(code_list) // 7
        frames = [code_list[7 * j: 7 * j + 7] for j in range(n_frames)]
        frames = [f for f in frames if all(0 <= c < _SNAC_CODEBOOK for c in f)]
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return self._snac_decode(frames)

    def _snac_decode(self, frames: List[List[int]]):
        """Run the SNAC codec on whole 7-token frames → mono float32 audio.

        Each frame fans out into SNAC's three hierarchical layers as
        ``[f0] / [f1,f4] / [f2,f3,f5,f6]``.
        """
        import torch

        layer_1: List[int] = []
        layer_2: List[int] = []
        layer_3: List[int] = []
        for f in frames:
            layer_1.append(f[0])
            layer_2.append(f[1]); layer_2.append(f[4])
            layer_3.append(f[2]); layer_3.append(f[3]); layer_3.append(f[5]); layer_3.append(f[6])

        dev = self.device
        codes = [
            torch.tensor(layer_1, device=dev).unsqueeze(0),
            torch.tensor(layer_2, device=dev).unsqueeze(0),
            torch.tensor(layer_3, device=dev).unsqueeze(0),
        ]
        with torch.inference_mode():
            audio = self.snac.decode(codes)
        return audio.detach().cpu().float().numpy().reshape(-1)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
        proc = self.proc
        self.proc = None
        if proc is not None and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    try:
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    except Exception:  # noqa: BLE001
                        pass
                proc.terminate()
                try:
                    proc.wait(timeout=8.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as exc:  # noqa: BLE001
                log.warning("error stopping Orpheus llama-server: %s", exc)
        job = self._job
        self._job = None
        if job is not None and sys.platform == "win32":
            try:
                import ctypes

                ctypes.WinDLL("kernel32").CloseHandle(int(job))
            except Exception:  # noqa: BLE001
                pass
        self.snac = None


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
