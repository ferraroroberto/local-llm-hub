"""Decode browser-recorded containers to 16 kHz mono WAV before an ASR backend
sees them.

Chrome and Android's ``MediaRecorder`` produce WebM/Opus (and Firefox Ogg/Opus)
by default. The Apple ``afconvert`` that fronts the parakeet worker cannot open
either, so the clip came back as a bare 400 while wav and mp4 transcribed. The
hub decodes these two containers once, here, so every ASR backend receives a
format it already handles. Every other container is forwarded untouched.

Detection is by the file's magic bytes, not its name or content type: browsers
label the blob freely (``audio/webm;codecs=opus``, ``video/webm``, no
extension), but the first four bytes of a WebM/Matroska file and an Ogg stream
are fixed.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
import wave
from pathlib import PurePath
from typing import Dict, Optional, Tuple

from fastapi import HTTPException

from src.concurrency.pools import MEDIA, run_on

from ..hub_observability import ERROR_BAD_REQUEST, ERROR_INTERNAL, tag_error
from ..no_window import NO_WINDOW

logger = logging.getLogger(__name__)

# EBML header: the first four bytes of every WebM and Matroska file.
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_OGG_MAGIC = b"OggS"

# A clip is decoded in a fraction of its own length; the bound only stops a
# wedged ffmpeg from holding the request (and its thread) forever.
_DECODE_TIMEOUT_S = 120.0

# The ``file`` part of a multipart body, up to its first content byte. Used to
# peek the container without parsing the whole form.
_FILE_PART_HEAD = re.compile(
    rb'name="file"[^\r\n]*\r?\n(?:[^\r\n]+\r?\n)*\r?\n(.{4})', re.DOTALL,
)

WhisperFiles = Dict[str, Tuple[str, bytes, str]]


def container_of(data: bytes) -> Optional[str]:
    """The browser container ``data`` starts with (``"webm"`` or ``"ogg"``), else
    ``None`` for anything the backends already read."""
    head = data[:4]
    if head == _EBML_MAGIC:
        return "webm"
    if head == _OGG_MAGIC:
        return "ogg"
    return None


def body_needs_decode(body: bytes) -> bool:
    """Cheap check on a raw multipart body: does its ``file`` part start with a
    container this module decodes? Lets the common case keep forwarding the
    request bytes verbatim without parsing the form."""
    match = _FILE_PART_HEAD.search(body[: 64 * 1024]) or _FILE_PART_HEAD.search(body)
    return bool(match) and container_of(match.group(1)) is not None


def _decode_to_wav(data: bytes, container: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise HTTPException(
            status_code=503,
            detail=f"cannot decode a {container} upload: ffmpeg is not installed on this hub host",
        )
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd, input=data, capture_output=True, timeout=_DECODE_TIMEOUT_S,
            creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"decoding the {container} upload timed out after {_DECODE_TIMEOUT_S:.0f} s",
        )
    except OSError as exc:
        raise tag_error(
            HTTPException(
                status_code=500, detail=f"could not run ffmpeg to decode the {container} upload: {exc}",
            ),
            ERROR_INTERNAL,
        )
    if proc.returncode != 0 or not proc.stdout:
        reason = proc.stderr.decode(errors="replace").strip().splitlines()
        raise tag_error(
            HTTPException(
                status_code=415,
                detail=(
                    f"cannot decode the uploaded {container} audio"
                    + (f": {reason[-1]}" if reason else " (no audio stream)")
                ),
            ),
            ERROR_BAD_REQUEST,
        )
    return _wav_from_pcm(proc.stdout)


def _wav_from_pcm(pcm: bytes) -> bytes:
    """Wrap 16 kHz mono s16le PCM in a finalised WAV header. ffmpeg cannot seek a
    pipe, so its own wav output leaves the length fields unset, which a strict
    reader (afconvert, whisper.cpp) treats as a corrupt file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(pcm)
    return buf.getvalue()


async def normalize_files(files: Optional[WhisperFiles]) -> Optional[WhisperFiles]:
    """Return ``files`` with a WebM/Ogg ``file`` entry replaced by 16 kHz mono
    WAV; any other upload is returned as given."""
    if not files or "file" not in files:
        return files
    name, data, _content_type = files["file"]
    container = container_of(data)
    if container is None:
        return files
    wav = await run_on(MEDIA, _decode_to_wav, data, container)
    logger.info(
        "ℹ️ audio normalised: %s upload (%d B) decoded to 16 kHz mono wav (%d B)",
        container, len(data), len(wav),
    )
    stem = PurePath(name).stem or "audio"
    return {**files, "file": (f"{stem}.wav", wav, "audio/wav")}
