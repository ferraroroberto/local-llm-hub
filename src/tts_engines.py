"""TTS synthesis engines for the hub's ``/v1/audio/speech`` backend.

Two engines behind one interface, selected per registry row's
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

Heavy deps (torch, chatterbox-tts, snac, soundfile) are imported **lazily
inside ``load``/``synthesize``** so this module imports cleanly under
pytest/CI where they are absent. Install them with ``requirements-tts.txt``
on TTS-enabled hosts (see ``scripts/install_tts.py``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
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

    # ---- lifecycle ----

    def load(self) -> None:
        import torch  # noqa: F401  (presence check; SNAC needs it)
        from snac import SNAC

        self.device = resolve_device(self.device_arg)
        log.info("loading SNAC codec on %s …", self.device)
        self.snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval().to(self.device)
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
        from .backend_process import _llama_server_binary, VENDOR_LLAMA
        from .server_process import WIN_NEW_GROUP

        self._reclaim_internal_port()
        bin_path = _llama_server_binary()
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

    def _prompt_for(self, req: SpeechRequest) -> str:
        voice = req.voice if req.voice in self.AVAILABLE_VOICES else self.DEFAULT_VOICE
        # Orpheus-FastAPI prompt convention for the llama.cpp route: the
        # end marker is the model's <|eot_id|> special token (not <|eot|>).
        return f"<|audio|>{voice}: {req.text}<|eot_id|>"

    @staticmethod
    def _completion_payload(prompt: str, stream: bool) -> dict:
        return {
            "prompt": prompt,
            "n_predict": 4096,
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
        prompt = self._prompt_for(req)
        url = f"http://127.0.0.1:{self.internal_port}/completion"
        r = httpx.post(url, json=self._completion_payload(prompt, stream=False), timeout=300.0)
        r.raise_for_status()
        content = r.json().get("content", "")
        codes = self._parse_tokens(content)
        if not codes:
            log.warning("Orpheus emitted no audio tokens for input")
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
        prompt = self._prompt_for(req)
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
        with httpx.stream(
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
    if eng == "orpheus":
        return OrpheusEngine(model, device)
    raise ValueError(
        f"unknown tts_engine {model.tts_engine!r} for model {model.id!r} "
        f"(expected 'chatterbox' or 'orpheus')"
    )
