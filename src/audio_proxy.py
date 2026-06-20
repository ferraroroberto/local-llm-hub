"""Shared multipart bridging for the whisper audio-translate proxy paths.

Two code paths accept an OpenAI-shaped multipart audio request and must hand
``whisper-server`` a byte-identical upstream request:

* the hub's ``_proxy_audio`` (``src/server.py``) — the observable :8000 proxy;
* the lazy-load shim (``src/whisper_translate_proxy.py``) — owns the on-demand
  translate child process.

``whisper-server`` exposes a single inference path
(``/v1/audio/transcriptions``) and honors whisper.cpp's own ``translate=true``
boolean rather than OpenAI's ``task=translate`` string. Both paths therefore
have to pick the single ``file`` upload (dropping any extra file parts —
whisper-server takes exactly one), bridge ``task`` → ``translate``, forward
every other field untouched, and rebuild the httpx ``files=`` / ``data=``
request. This module is the single home for that contract so the two callers
cannot silently diverge (issue #132).

Per-caller concerns stay at the call site: each parses the form with its own
error shape, the lazy shim injects its row's default language (#128), and each
owns its upstream URL, timeout, observability stashing and error responses.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from starlette.datastructures import FormData, UploadFile

# httpx ``files=`` mapping: field name -> (filename, bytes, content-type).
WhisperFiles = Dict[str, Tuple[str, bytes, str]]


async def build_whisper_upstream_request(
    form: FormData,
) -> Tuple[Optional[UploadFile], Dict[str, str], Optional[WhisperFiles]]:
    """Bridge an OpenAI-shaped audio form into a whisper-server upstream request.

    Returns ``(upload, data, files)`` where:

    * ``upload`` is the single ``file`` part, or ``None`` if the caller sent
      none — each caller raises its own "missing file" error.
    * ``data`` is the non-file form fields, with ``task=translate`` rewritten to
      ``translate=true`` and ``task=transcribe`` (whisper-server's default)
      dropped; every other field is forwarded verbatim.
    * ``files`` is the httpx ``files=`` dict built from ``upload`` (its bytes are
      read here), or ``None`` when there is no upload.
    """
    upload: Optional[UploadFile] = None
    data: Dict[str, str] = {}
    for key, value in form.multi_items():
        if isinstance(value, UploadFile):
            if key == "file" and upload is None:
                upload = value
            # Drop any extra file parts — whisper-server takes exactly one.
            continue
        if key == "task":
            if value == "translate":
                data["translate"] = "true"
            # task=transcribe is whisper-server's default; drop silently.
            continue
        data[key] = value

    files: Optional[WhisperFiles] = None
    if upload is not None:
        file_bytes = await upload.read()
        files = {
            "file": (
                upload.filename or "audio",
                file_bytes,
                upload.content_type or "application/octet-stream",
            )
        }
    return upload, data, files
