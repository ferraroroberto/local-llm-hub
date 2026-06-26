"""Audio proxy routes (``/v1/audio/*``).

Split out of ``server.py``: the whisper transcription / translation proxy and
the TTS speech proxy together were ~300 lines of multipart-and-httpx plumbing
sitting between the chat routes. The whisper-server and the TTS shim already
speak the OpenAI ``/v1/audio/*`` shape, so the hub mostly forwards bytes — the
point of routing through here (rather than hitting :8090/:8091/:8093 directly)
is that the observability middleware records the request in the live ring.

Routes are collected on a module-level :class:`fastapi.APIRouter` and mounted
onto the parent hub app by ``server.py`` via ``include_router``.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .audio_proxy import build_whisper_upstream_request
from .server_common import current_otel_span, safe_span, stash_trace_id_on_ctx

logger = logging.getLogger(__name__)

router = APIRouter()


def _whisper_port_for_model(model_name: str, *, default_role: str) -> Optional[int]:
    """Pick a whisper backend port for a request.

    If the caller passed ``model=...`` in the multipart form, try to
    resolve it through the registry first. Otherwise fall back to a
    heuristic based on the endpoint's role:

      * ``audio_transcribe`` → first whisper backend whose id does NOT
        contain "translate" (the turbo / GPU one).
      * ``audio_translate`` → first whisper backend whose id DOES
        contain "translate" (the medium / CPU sibling).

    Returns ``None`` if no whisper backend is enabled on this host —
    the caller surfaces that as 503.
    """
    from .model_registry import enabled_models, resolve as _resolve_model

    if model_name:
        m = _resolve_model(model_name)
        if m and m.backend == "whisper" and m.port:
            return m.port

    whispers = [m for m in enabled_models() if m.backend == "whisper" and m.port]
    if not whispers:
        return None

    if default_role == "audio_translate":
        for m in whispers:
            if "translate" in m.id.lower():
                return m.port
    else:  # audio_transcribe — anything that isn't the translate sibling
        for m in whispers:
            if "translate" not in m.id.lower():
                return m.port

    return whispers[0].port


async def _proxy_audio(request: Request, *, default_role: str, ctx_path: str) -> Response:
    """Stream a multipart audio request through to a whisper backend.

    The whisper-server already speaks the OpenAI ``/v1/audio/*`` shape,
    so we just forward the bytes + headers and pass the response back.
    The hub's observability middleware records the request in the live
    ring — that's the whole point of going through us instead of
    hitting :8090 / :8091 directly.

    For ``audio_translate`` requests the raw-bytes path cannot be used:
    whisper-server exposes exactly one inference endpoint
    (``/v1/audio/transcriptions``), and it expects whisper.cpp's own
    ``translate=true`` boolean rather than OpenAI's ``task=translate``
    string field.  We therefore parse the multipart form, rewrite
    ``task=translate`` → ``translate=true``, and POST to the backend's
    real ``/v1/audio/transcriptions`` path — mirroring the logic the
    lazy-load shim in ``whisper_translate_proxy.py`` already uses.
    """
    import httpx as _httpx

    body = await request.body()

    # Peek the ``model`` field out of the multipart body to choose a
    # backend. python-multipart parsing is overkill — the field shows
    # up as ``Content-Disposition: form-data; name="model"`` followed
    # by a couple of CRLF lines and the value. Best-effort regex.
    #
    # Scan the first 16 KB first (the cheap path — standard SDK clients
    # serialize plain form fields before the file part, so ``model``
    # lands in the head). Fall back to the whole body if it's not there:
    # a client that puts a large file *before* the model field would
    # otherwise misroute to the default turbo (#128) — silently landing
    # on the glossary path the caller chose ``whisper-vanilla`` to escape.
    model_name = ""
    try:
        import re as _re
        pattern = rb'name="model"\r?\n\r?\n([^\r\n]+)'
        match = _re.search(pattern, body[: 16 * 1024]) or _re.search(pattern, body)
        if match:
            model_name = match.group(1).decode("ascii", errors="ignore").strip()
    except Exception:  # noqa: BLE001
        pass

    port = _whisper_port_for_model(model_name, default_role=default_role)
    if port is None:
        raise HTTPException(
            status_code=503,
            detail="no whisper backend enabled on this host",
        )

    ctx = getattr(request.state, "obs_ctx", None)
    if ctx is not None:
        ctx.model = model_name
        ctx.backend = "whisper"

    span = current_otel_span()
    if span is not None and hasattr(span, "set_attribute"):
        with safe_span("whisper_attrs"):
            span.set_attribute("gen_ai.system", "whisper")
            span.set_attribute("gen_ai.operation.name", default_role)
            if model_name:
                span.set_attribute("gen_ai.request.model", model_name)
            span.set_attribute("whisper.port", int(port))
    stash_trace_id_on_ctx(ctx, span)

    if default_role == "audio_translate":
        # whisper-server exposes a single inference path (/v1/audio/transcriptions)
        # and uses whisper.cpp's own `translate=true` boolean, not OpenAI's
        # `task=translate` string. Parse the multipart form, then bridge it to
        # the upstream request via the shared helper (the lazy-load shim in
        # whisper_translate_proxy.py calls the same helper — issue #132).
        try:
            form = await request.form()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"invalid multipart body: {exc}",
            )

        upload, data, files = await build_whisper_upstream_request(form)
        if upload is None:
            raise HTTPException(
                status_code=400,
                detail="missing required form field: file",
            )

        upstream_url = f"http://127.0.0.1:{port}/v1/audio/transcriptions"
        try:
            async with _httpx.AsyncClient(timeout=300.0) as client:
                upstream = await client.post(upstream_url, files=files, data=data)
        except _httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"whisper upstream error: {exc}")
    else:
        upstream_url = f"http://127.0.0.1:{port}{ctx_path}"
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() in {"content-type", "accept"}
        }
        try:
            async with _httpx.AsyncClient(timeout=300.0) as client:
                upstream = await client.post(upstream_url, content=body, headers=headers)
        except _httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"whisper upstream error: {exc}")

    # Apply the committed transcription glossary (issue #90) to the
    # transcript text before returning. Deterministic literal fixes for
    # acoustically-strong errors recognition-level biasing can't solve
    # (e.g. "cloud code" → "Claude Code"). Wrapped defensively: a broken
    # glossary must never break the passthrough.
    out_content = upstream.content
    if upstream.status_code == 200:
        try:
            from .transcription_glossary import apply_to_response, load_rules

            rules = load_rules()
            if rules:
                out_content = apply_to_response(
                    upstream.content,
                    upstream.headers.get("content-type"),
                    rules,
                )
        except Exception:  # noqa: BLE001 — never let post-processing fail the proxy
            out_content = upstream.content

    out_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in {"content-length", "transfer-encoding", "connection"}
    }
    return Response(
        content=out_content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=out_headers,
    )


@router.post("/v1/audio/transcriptions")
async def audio_transcriptions(request: Request) -> Response:
    """Proxy transcription requests through the hub so they land in the
    observability ring. Clients that point directly at :8090 still
    work but are invisible to the admin UI — pointing at :8000 here
    makes them visible without changing the request shape.
    """
    return await _proxy_audio(
        request, default_role="audio_transcribe",
        ctx_path="/v1/audio/transcriptions",
    )


@router.post("/v1/audio/translations")
async def audio_translations(request: Request) -> Response:
    """Companion to :func:`audio_transcriptions` for the ``task=translate``
    case. Routes to the ``audio_translate`` role's port (medium, CPU).
    """
    return await _proxy_audio(
        request, default_role="audio_translate",
        ctx_path="/v1/audio/translations",
    )


def _tts_port_for_model(model_name: str) -> Optional[int]:
    """Pick a TTS backend port for a ``/v1/audio/speech`` request.

    Resolve an explicit ``model`` through the registry first; otherwise fall
    back to the ``audio_speech`` role (chatterbox), then the first enabled
    TTS backend. Returns ``None`` if no TTS backend is enabled on this host.
    """
    from .model_registry import enabled_models, resolve as _resolve_model

    if model_name:
        m = _resolve_model(model_name)
        if m and m.backend == "tts" and m.port:
            return m.port

    tts = [m for m in enabled_models() if m.backend == "tts" and m.port]
    if not tts:
        return None
    for m in tts:
        if "audio_speech" in (m.aliases or []):
            return m.port
    return tts[0].port


@router.post("/v1/audio/speech")
async def audio_speech(request: Request) -> Response:
    """Proxy text-to-speech requests through the hub so they land in the
    observability ring. The inverse of :func:`audio_transcriptions`.

    Body is the OpenAI JSON shape ``{model, input, voice, response_format,
    speed}`` (plus Chatterbox's ``exaggeration`` / ``cfg_weight``). Clients
    may also POST directly to the backend port (:8092 / :8093) for lower
    overhead, bypassing the hub's capture.
    """
    import json as _json

    import httpx as _httpx

    body = await request.body()
    model_name = ""
    stream_format = ""
    try:
        parsed = _json.loads(body or b"{}")
        if isinstance(parsed, dict):
            model_name = str(parsed.get("model") or "")
            stream_format = str(parsed.get("stream_format") or "").strip().lower()
    except Exception:  # noqa: BLE001
        pass

    port = _tts_port_for_model(model_name)
    if port is None:
        raise HTTPException(status_code=503, detail="no TTS backend enabled on this host")

    ctx = getattr(request.state, "obs_ctx", None)
    if ctx is not None:
        ctx.model = model_name
        ctx.backend = "tts"

    span = current_otel_span()
    if span is not None and hasattr(span, "set_attribute"):
        with safe_span("tts_attrs"):
            span.set_attribute("gen_ai.system", "tts")
            span.set_attribute("gen_ai.operation.name", "audio_speech")
            if model_name:
                span.set_attribute("gen_ai.request.model", model_name)
            span.set_attribute("tts.port", int(port))
    stash_trace_id_on_ctx(ctx, span)

    upstream_url = f"http://127.0.0.1:{port}/v1/audio/speech"
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() in {"content-type", "accept"}
    }

    def _passthrough_headers(upstream) -> dict:
        return {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in {"content-length", "transfer-encoding", "connection"}
        }

    # Streaming synth: hold the upstream connection open and forward bytes as
    # they arrive, so time-to-first-audio stays low. The obs middleware still
    # records this entry on response, exactly like the chat-stream path.
    if stream_format == "audio":
        client = _httpx.AsyncClient(timeout=300.0)
        stream_cm = client.stream("POST", upstream_url, content=body, headers=headers)
        try:
            upstream = await stream_cm.__aenter__()
        except _httpx.HTTPError as exc:
            await client.aclose()
            raise HTTPException(status_code=502, detail=f"tts upstream error: {exc}")

        async def _forward():
            try:
                async for piece in upstream.aiter_bytes():
                    yield piece
            finally:
                await stream_cm.__aexit__(None, None, None)
                await client.aclose()

        return StreamingResponse(
            _forward(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
            headers=_passthrough_headers(upstream),
        )

    try:
        async with _httpx.AsyncClient(timeout=300.0) as client:
            upstream = await client.post(upstream_url, content=body, headers=headers)
    except _httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"tts upstream error: {exc}")

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=_passthrough_headers(upstream),
    )
