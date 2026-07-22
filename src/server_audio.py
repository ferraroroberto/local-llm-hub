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
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .audio_proxy import build_whisper_upstream_request
from .http_client import get_async_client
from .model_registry import Model
from .remote_proxy import remote_auth_token, remote_base_url
from .server_common import current_otel_span, safe_span, stash_trace_id_on_ctx

logger = logging.getLogger(__name__)

router = APIRouter()


def _remote_audio_headers(model: Model) -> Optional[dict]:
    """``Authorization`` header for a remote-hub audio call, mirrors
    ``server._remote_headers`` — kept local to avoid a circular import
    (``server.py`` imports this module's router).
    """
    token = remote_auth_token(model.host) if model.host else None
    return {"Authorization": f"Bearer {token}"} if token else None


def _audio_upstream_error(exc: Exception, *, backend: str, port: int) -> HTTPException:
    """Map an httpx upstream failure to a *distinct* HTTPException.

    A connection failure — the backend port refuses the socket or never
    answers — is a categorically different condition from a transient
    mid-flight error: the backend is wholesale down (crashed, not started, or
    lost its mutex-shared port), not merely slow. Surface it as a ``503`` whose
    message names the port and says the backend isn't running, instead of the
    opaque ``502 "whisper upstream error: All connection attempts failed"`` that
    gave downstream consumers no way to tell "down" from "in flight past
    timeout" (issue #147). Every other upstream error stays a ``502``.
    """
    import httpx as _httpx

    if isinstance(exc, (_httpx.ConnectError, _httpx.ConnectTimeout)):
        logger.warning(
            "⚠️ %s not reachable on :%s — backend not running (connection refused)",
            backend, port,
        )
        return HTTPException(
            status_code=503,
            detail=(
                f"{backend} not running on :{port} — start the backend "
                f"(admin Models tab or its launcher) and retry"
            ),
        )
    return HTTPException(status_code=502, detail=f"{backend} upstream error: {exc}")


def _whisper_model_for_request(model_name: str, *, default_role: str) -> Optional[Model]:
    """Pick a whisper-shaped backend for a request.

    If the caller passed ``model=...`` in the multipart form, try to
    resolve it through the registry first — this is the only path that
    can return a *remote* (``host:`` set) model, e.g. ``model=parakeet``.
    Otherwise fall back to a heuristic based on the endpoint's role,
    restricted to locally-owned backends only — the default role never
    silently starts proxying to a remote host:

      * ``audio_transcribe`` → first whisper backend whose id does NOT
        contain "translate" (the turbo / GPU one).
      * ``audio_translate`` → first whisper backend whose id DOES
        contain "translate" (the medium / CPU sibling).

    Returns ``None`` if no whisper backend is enabled on this host —
    the caller surfaces that as 503.
    """
    from .model_registry import local_models, resolve as _resolve_model

    if model_name:
        m = _resolve_model(model_name)
        if m and m.backend == "whisper" and m.port:
            return m

    whispers = [m for m in local_models() if m.backend == "whisper" and m.port]
    if not whispers:
        return None

    if default_role == "audio_translate":
        for m in whispers:
            if "translate" in m.id.lower():
                return m
    else:  # audio_transcribe — anything that isn't the translate sibling
        for m in whispers:
            if "translate" not in m.id.lower():
                return m

    return whispers[0]


class _BackendUnavailable(Exception):
    """Raised inside a single candidate attempt when that backend is *down* —
    a connection error/timeout or an upstream 502/503/504 — so ``_proxy_audio``
    can fail over to the next model in the role chain (#348). Carries the
    HTTPException to surface if this turns out to be the last candidate."""

    def __init__(self, http_exc: HTTPException) -> None:
        self.http_exc = http_exc


# Role aliases a caller may send as ``model=`` to explicitly ask for the
# failover chain; a *concrete* model id is honoured single-shot instead.
_ROLE_ALIASES = {"audio_transcribe", "audio_translate"}


def _whisper_chain_for_request(model_name: str, *, default_role: str) -> List[Model]:
    """Ordered whisper-shaped candidates to try for this request (#348).

    * An explicit **concrete** model (``model=whisper-vanilla``) → a one-element
      chain: honour it exactly, never fail over. Preserves #128 — a caller that
      picked ``whisper-vanilla`` to escape the glossary must not silently land
      on turbo.
    * The **role** path (no ``model``, or a role alias like ``audio_transcribe``)
      → the configured ``roles.audio.<role>`` chain (``model_id`` + ``fallback``),
      resolved via the registry so it can include remote/cross-enabled rows
      (e.g. ``parakeet`` on the Mac).
    * If the config chain is empty/unresolvable → the legacy local-only heuristic
      (:func:`_whisper_model_for_request`), so nothing regresses.
    """
    from .model_registry import audio_role_chain, resolve as _resolve_model

    if model_name and model_name not in _ROLE_ALIASES:
        m = _resolve_model(model_name)
        if m and m.backend == "whisper" and m.port:
            return [m]
        # unresolvable / non-whisper explicit id → fall through to the role chain

    role_key = "translate" if default_role == "audio_translate" else "transcribe"
    chain: List[Model] = []
    seen: set = set()
    for mid in audio_role_chain(role_key):
        m = _resolve_model(mid)
        if m and m.backend == "whisper" and m.port and m.id not in seen:
            seen.add(m.id)
            chain.append(m)
    if chain:
        return chain

    m = _whisper_model_for_request(model_name, default_role=default_role)
    return [m] if m is not None else []


async def _proxy_audio(request: Request, *, default_role: str, ctx_path: str) -> Response:
    """Forward a multipart audio request to a whisper backend, failing over
    across the role's model chain (#348).

    The whisper-server already speaks the OpenAI ``/v1/audio/*`` shape, so we
    forward the bytes/form and pass the response back — the point of going
    through the hub (vs hitting :8090/:8091/:8098 directly) is the observability
    ring. When the primary model's backend is *unavailable* (a connection
    error/timeout or a 502/503/504), the request transparently retries the next
    model in ``roles.audio.<role>`` instead of erroring — so a dead
    ``parakeet@mac`` silently falls through to whisper, never a failed
    dictation. A real client error (4xx) or a 200 is returned as-is.

    For ``audio_translate`` the raw-bytes path can't be used: whisper-server
    exposes a single inference endpoint (``/v1/audio/transcriptions``) and wants
    whisper.cpp's ``translate=true`` boolean, not OpenAI's ``task=translate``.
    The form is parsed+rewritten once up front; its file bytes are read into
    ``files`` there, so the payload is safe to resend to each candidate.
    """
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

    chain = _whisper_chain_for_request(model_name, default_role=default_role)
    if not chain:
        raise HTTPException(status_code=503, detail="no whisper backend enabled on this host")

    # Build the reusable upstream payload once — bytes-based, so it can be
    # resent to each candidate without re-reading the request stream.
    if default_role == "audio_translate":
        # whisper-server exposes a single inference path and wants whisper.cpp's
        # `translate=true` boolean, not OpenAI's `task=translate` string. Parse +
        # rewrite via the shared helper (the lazy-load shim in
        # whisper_translate_proxy.py calls the same one — issue #132).
        try:
            form = await request.form()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid multipart body: {exc}")
        upload, data, files = await build_whisper_upstream_request(form)
        if upload is None:
            raise HTTPException(status_code=400, detail="missing required form field: file")
        send = {"files": files, "data": data}
        upstream_path = "/v1/audio/transcriptions"
    else:
        fwd_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() in {"content-type", "accept"}
        }
        send = {"content": body, "headers": fwd_headers}
        upstream_path = ctx_path

    ctx = getattr(request.state, "obs_ctx", None)
    span = current_otel_span()
    client = get_async_client()  # fetch once, reuse for every candidate

    last_http_exc: Optional[HTTPException] = None
    for idx, target in enumerate(chain):
        is_last = idx == len(chain) - 1
        try:
            return await _forward_to_candidate(
                target, send, upstream_path, default_role, model_name,
                ctx, span, client, connect_fast=not is_last,
            )
        except _BackendUnavailable as bu:
            last_http_exc = bu.http_exc
            if not is_last:
                logger.warning(
                    "🔁 audio failover: %s unavailable (%s) — trying next in chain",
                    target.id, bu.http_exc.status_code,
                )
            continue
    raise last_http_exc or HTTPException(status_code=503, detail="no audio backend answered")


async def _forward_to_candidate(
    target: Model, send: dict, upstream_path: str, default_role: str,
    model_name: str, ctx, span, client, *, connect_fast: bool,
) -> Response:
    """POST the prepared payload to one candidate backend and return its
    Response, or raise :class:`_BackendUnavailable` when the backend is down so
    the caller can try the next model (#348). Records obs/OTel against the model
    that actually served, and applies the #90 glossary to a 200 transcript."""
    import httpx as _httpx

    port = target.port
    remote = remote_base_url(target)

    if ctx is not None:
        ctx.model = model_name or target.id
        ctx.backend = "whisper"
    if span is not None and hasattr(span, "set_attribute"):
        with safe_span("whisper_attrs"):
            span.set_attribute("gen_ai.system", "whisper")
            span.set_attribute("gen_ai.operation.name", default_role)
            span.set_attribute("gen_ai.request.model", model_name or target.id)
            span.set_attribute("whisper.port", int(port))
            span.set_attribute("whisper.model_id", target.id)
    stash_trace_id_on_ctx(ctx, span)

    base = remote if remote else f"http://127.0.0.1:{port}"
    url = f"{base}{upstream_path}"
    headers = dict(send.get("headers") or {})
    if remote:
        headers.update(_remote_audio_headers(target) or {})
    # A dead primary should fail over fast (short connect); the last resort keeps
    # a patient connect. Read stays long — a transcription can take a while.
    timeout = _httpx.Timeout(4.0 if connect_fast else 30.0, read=300.0, write=60.0, pool=10.0)
    post_kwargs: dict = {"headers": headers or None, "timeout": timeout}
    if "files" in send:
        post_kwargs["files"] = send["files"]
        post_kwargs["data"] = send["data"]
    else:
        post_kwargs["content"] = send["content"]

    try:
        upstream = await client.post(url, **post_kwargs)
    except _httpx.HTTPError as exc:
        raise _BackendUnavailable(_audio_upstream_error(exc, backend="whisper-server", port=port))
    if upstream.status_code in (502, 503, 504):
        raise _BackendUnavailable(HTTPException(
            status_code=upstream.status_code,
            detail=f"whisper backend {target.id} unavailable ({upstream.status_code})",
        ))

    # Apply the committed transcription glossary (issue #90) to a 200 transcript
    # before returning. Deterministic literal fixes (e.g. "cloud code" →
    # "Claude Code") for acoustically-strong errors biasing can't solve. Wrapped
    # defensively: a broken glossary must never break the passthrough.
    out_content = upstream.content
    if upstream.status_code == 200:
        try:
            from .transcription_glossary import apply_to_response, load_rules

            rules = load_rules()
            if rules:
                out_content = apply_to_response(
                    upstream.content, upstream.headers.get("content-type"), rules,
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


@router.get("/v1/audio/health")
def audio_health() -> Response:
    """Probe-only liveness of the audio backends — lets a consumer preflight
    instead of discovering an outage one failed transcription at a time (#147).

    Reports each enabled whisper / TTS backend with its port and whether it is
    currently reachable (a cheap GET to the backend, never a transcription).
    ``status`` is ``ok`` when every enabled audio backend answers, ``degraded``
    when at least one is down, and ``none`` when no audio backend is enabled on
    this host. A degraded/none result returns HTTP 503 so a consumer can branch
    on the status code alone; ``ok`` returns 200.

    Defined as a sync route on purpose: ``is_reachable`` does blocking socket
    probes, so FastAPI runs it in a threadpool rather than stalling the loop.
    """
    import json as _json

    from .backend_process import is_reachable
    from .model_registry import local_models

    backends = []
    # Local backends only — a remote-owned row's liveness is the owning
    # host's own /v1/audio/health concern, not something this loopback
    # probe can answer correctly (see app_web/routers/models.py for the
    # cross-host merge that *does* surface remote rows, in the admin UI).
    audio = [m for m in local_models() if m.backend in ("whisper", "tts") and m.port]
    for m in audio:
        reachable = is_reachable(m, timeout=1.0)
        backends.append({
            "id": m.id,
            "backend": m.backend,
            "port": m.port,
            "reachable": reachable,
        })

    if not backends:
        status, code = "none", 503
    elif all(b["reachable"] for b in backends):
        status, code = "ok", 200
    else:
        status, code = "degraded", 503

    return Response(
        content=_json.dumps({"status": status, "backends": backends}),
        status_code=code,
        media_type="application/json",
    )


def _tts_model_for_request(model_name: str) -> Optional[Model]:
    """Pick a TTS backend for a ``/v1/audio/speech`` request.

    Resolve an explicit ``model`` through the registry first — the only path
    that can return a *remote* (``host:`` set) model, e.g. ``model=mac_say``.
    An unresolvable explicit model returns ``None`` rather than silently
    selecting an English backend. Only an omitted model falls back to the
    ``audio_speech`` role (Piper), then the first
    enabled *local* TTS backend — the default role never silently proxies to
    a remote host. Returns ``None`` if no TTS backend is enabled on this host.
    """
    from .model_registry import local_models, resolve as _resolve_model

    if model_name:
        m = _resolve_model(model_name)
        if m and m.backend == "tts" and m.port:
            return m
        return None

    tts = [m for m in local_models() if m.backend == "tts" and m.port]
    if not tts:
        return None
    for m in tts:
        if "audio_speech" in (m.aliases or []):
            return m
    return tts[0]


@router.post("/v1/audio/speech")
async def audio_speech(request: Request) -> Response:
    """Proxy text-to-speech requests through the hub so they land in the
    observability ring. The inverse of :func:`audio_transcriptions`.

    Body is the OpenAI JSON shape ``{model, input, voice, response_format,
    speed}`` (plus Chatterbox's ``exaggeration`` / ``cfg_weight``). Clients
    may also POST directly to the backend port (:8096 / :8092 / :8093 / :8095) for lower
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

    target = _tts_model_for_request(model_name)
    if target is None:
        if model_name:
            raise HTTPException(
                status_code=400,
                detail=f"unknown or unsupported TTS model: {model_name}",
            )
        raise HTTPException(status_code=503, detail="no TTS backend enabled on this host")
    port = target.port
    remote = remote_base_url(target)

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

    upstream_url = f"{remote}/v1/audio/speech" if remote else f"http://127.0.0.1:{port}/v1/audio/speech"
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() in {"content-type", "accept"}
    }
    if remote:
        headers.update(_remote_audio_headers(target) or {})

    def _passthrough_headers(upstream) -> dict:
        return {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in {"content-length", "transfer-encoding", "connection"}
        }

    # Streaming synth: hold the upstream connection open and forward bytes as
    # they arrive, so time-to-first-audio stays low. The obs middleware still
    # records this entry on response, exactly like the chat-stream path.
    if stream_format == "audio":
        client = get_async_client()
        stream_cm = client.stream("POST", upstream_url, content=body, headers=headers)
        try:
            upstream = await stream_cm.__aenter__()
        except _httpx.HTTPError as exc:
            raise _audio_upstream_error(exc, backend="tts-server", port=port)

        async def _forward():
            try:
                async for piece in upstream.aiter_bytes():
                    yield piece
            finally:
                await stream_cm.__aexit__(None, None, None)

        return StreamingResponse(
            _forward(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
            headers=_passthrough_headers(upstream),
        )

    try:
        client = get_async_client()
        upstream = await client.post(upstream_url, content=body, headers=headers)
    except _httpx.HTTPError as exc:
        raise _audio_upstream_error(exc, backend="tts-server", port=port)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=_passthrough_headers(upstream),
    )
