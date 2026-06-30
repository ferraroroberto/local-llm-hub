"""Local multi-model hub: Anthropic-compatible and OpenAI-compatible endpoints.

Each request resolves its `model` field against `config/models.yaml`
(by registry id, display_name, or alias) and routes by the resolved
row's `backend`. The active routes on the reference CUDA host:

- claude_haiku / claude_sonnet / claude_opus
                          -> `claude -p` subprocess (Anthropic subscription)
- gemini_pro / gemini_flash / gemini_lite
                          -> `agy` Antigravity CLI (Google AI Pro subscription)
- agentic_light / qwen3.5-4b
                          -> llama-server at 127.0.0.1:8088 (/v1)
- agentic_heavy / gemma4-26b-a4b-it
                          -> llama-server at 127.0.0.1:8087 (/v1)
- whisper-large-v3-turbo / whisper-medium-translate
                          -> whisper-server on :8090 / :8091 (/v1/audio/*)

(qwen3.5-9b on :8081, glm-4.5-air on :8082 and gemma4-e4b-it on :8086
remain defined as ad-hoc / fallback candidates but are out of the
active rotation — not in any host's `enabled:` list.)

Two shapes exposed:
  * POST /v1/messages          - Anthropic shape (drop-in for the SDK)
  * POST /v1/chat/completions  - OpenAI shape (passthrough/translation)
  * GET  /v1/models            - union of enabled names (both shapes)

Caveats: image content blocks work on the claude-* and gemini-* paths
(decoded to a per-request temp dir); local llama-server backends are
text-only and 400 on image input. No tool_use round-trip on the
Anthropic shape for non-claude backends (OpenAI-shape callers get tool
use natively from llama-server). Streaming: ``/v1/chat/completions``
proxies upstream SSE through (with ``<think>`` blocks stripped for
reasoning models); ``/v1/messages`` still returns a single JSON for
``stream=true`` until the Anthropic event translation lands.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

# Load .env *before* importing anything that reads env at module-import
# time (observability.py reads OTEL_* / LANGFUSE_* immediately on
# init_otel()). Soft-fails when python-dotenv isn't installed — the
# hub still runs, just without auto-loading the project .env file.
try:
    from dotenv import load_dotenv as _load_dotenv

    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path, override=False)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

from .claude_cli import ClaudeCLIError, call_claude
from .gemini_cli import GeminiCLIError, call_gemini
from .host_profile import hub_bind_host, hub_port
from .hub_log import HUB_LOG, install_root_handler
from .hub_observability import OBS, ObservatoryMiddleware
from .model_registry import Model, enabled_models
from .observability import (
    genai_meters,
    init_otel,
    instrument_fastapi_app,
    record_genai_metrics,
    set_genai_payload,
    set_genai_request_attrs,
    set_genai_response_attrs,
)
from .server_common import (
    client_id_from as _client_id_from,
    current_otel_span as _current_otel_span,
    resolve_model_or_400 as _resolve,
    safe_span as _safe_span,
    stash_trace_id_on_ctx as _stash_trace_id_on_ctx,
)
from .server_audio import router as _audio_router
from .server_images import router as _images_router
from .openai_upstream import (
    UpstreamError,
    anthropic_to_openai_messages,
    call_openai_chat,
    call_openai_chat_stream,
    clean_openai_response,
    iter_cleaned_sse,
    openai_to_anthropic_envelope,
)
from .trace_id_middleware import TraceIdHeaderMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
# Wire the in-memory ring handler so the admin webapp's Hub tab can tail
# both our logs and uvicorn's (access + error) without re-reading stdout.
install_root_handler()
logger = logging.getLogger(__name__)

# Bring up OpenTelemetry (issue #4). Soft-fails if the SDK or the OTLP
# endpoint is unreachable — the hub keeps serving traffic and the SPA's
# Telemetry tab shows "stack offline" until Langfuse comes up.
init_otel("local-llm-hub")


# ---- shared content-block helpers (unchanged shape) ----

class ContentBlock(BaseModel):
    type: str
    text: Optional[str] = None
    # Anthropic image block: {"type": "image", "source": {"type": "base64",
    # "media_type": "image/png", "data": "<b64>"}} or {"type": "url",
    # "url": "https://..."}. Kept loose to forward fields we don't model.
    source: Optional[Dict[str, Any]] = None


class Message(BaseModel):
    role: str
    content: Union[str, List[ContentBlock]]


class MessagesRequest(BaseModel):
    model: str
    messages: List[Message]
    max_tokens: Optional[int] = None
    system: Optional[Union[str, List[ContentBlock]]] = None
    stream: bool = False
    temperature: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


def _content_to_text(content: Union[str, List[ContentBlock]]) -> str:
    if isinstance(content, str):
        return content
    parts: List[str] = []
    for block in content:
        if block.type == "text" and block.text:
            parts.append(block.text)
    return "\n".join(parts)


def _system_to_text(system: Optional[Union[str, List[ContentBlock]]]) -> Optional[str]:
    if system is None:
        return None
    if isinstance(system, str):
        return system
    return _content_to_text(system) or None


_EXT_BY_MEDIA_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "application/pdf": "pdf",
    # Text/data document types — the CLI paths can attach any file, so a
    # caller may send a `document` block carrying one of these. Unknown
    # media types fall back to `.bin`, which the CLIs still read as bytes.
    "text/plain": "txt",
    "text/markdown": "md",
    "application/json": "json",
    "text/csv": "csv",
    "application/xml": "xml",
    "text/xml": "xml",
    "text/html": "html",
    "application/x-yaml": "yaml",
    "text/yaml": "yaml",
}

# Content-block types extracted to temp files for the multimodal CLI paths,
# mapped to (filename stem, default media_type when the block omits one).
_MEDIA_BLOCK_TYPES = {
    "image": ("img", "image/png"),
    "document": ("doc", "application/pdf"),
}


@contextmanager
def _extract_media_blocks(
    messages: List[Message],
) -> Iterator[Tuple[List[Message], List[Path]]]:
    """Pull media content blocks out of messages, write them to a temp dir.

    Handles Anthropic-style ``image`` and ``document`` (PDF) blocks. Yields
    ``(stripped_messages, attachment_paths)``. Stripped messages keep only
    text blocks so the existing flattener works unchanged. The temp dir and
    its contents are removed when the context exits, which must not happen
    until after the backend subprocess returns.

    Only ``source.type == "base64"`` blocks are written to disk.
    ``source.type == "url"`` is forwarded as a text reference to the URL
    since neither CLI fetches remote URLs on our behalf — fetching needs
    `httpx.get` first, which we can add later if a caller actually needs it.
    """
    attachment_paths: List[Path] = []
    stripped: List[Message] = []
    has_media = any(
        isinstance(m.content, list)
        and any(b.type in _MEDIA_BLOCK_TYPES for b in m.content)
        for m in messages
    )

    if not has_media:
        # Fast path — no temp dir at all when there's nothing to extract.
        yield messages, []
        return

    with tempfile.TemporaryDirectory(prefix="hub-media-") as td:
        td_path = Path(td)
        for msg in messages:
            if isinstance(msg.content, str):
                stripped.append(msg)
                continue
            kept: List[ContentBlock] = []
            for block in msg.content:
                if block.type not in _MEDIA_BLOCK_TYPES or not block.source:
                    kept.append(block)
                    continue
                stem, default_media = _MEDIA_BLOCK_TYPES[block.type]
                src = block.source
                stype = src.get("type")
                if stype == "base64":
                    data_b64 = src.get("data") or ""
                    media = src.get("media_type", default_media)
                    ext = _EXT_BY_MEDIA_TYPE.get(media, "bin")
                    fname = f"{stem}_{len(attachment_paths)}.{ext}"
                    fpath = td_path / fname
                    try:
                        fpath.write_bytes(base64.b64decode(data_b64))
                    except Exception as e:
                        raise HTTPException(
                            status_code=400,
                            detail=f"bad {block.type} block: {e}",
                        )
                    attachment_paths.append(fpath)
                elif stype == "url":
                    url = src.get("url", "")
                    kept.append(
                        ContentBlock(type="text", text=f"[{block.type} url: {url}]")
                    )
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"unsupported {block.type} source.type {stype!r}",
                    )
            # Keep at least an empty text block so flatteners don't crash.
            if not kept:
                kept = [ContentBlock(type="text", text="")]
            stripped.append(Message(role=msg.role, content=kept))
        yield stripped, attachment_paths


def _flatten_messages(messages: List[Message]) -> str:
    """Flatten multi-turn into one prompt for `claude -p` (Claude path only)."""
    if not messages:
        raise ValueError("messages must not be empty")
    if len(messages) == 1 and messages[0].role == "user":
        return _content_to_text(messages[0].content)
    lines: List[str] = ["Previous conversation:"]
    for m in messages[:-1]:
        label = "User" if m.role == "user" else "Assistant"
        lines.append(f"{label}: {_content_to_text(m.content)}")
    last = messages[-1]
    lines.append("")
    lines.append(f"Current {last.role} message:")
    lines.append(_content_to_text(last.content))
    return "\n".join(lines)


def _envelope_to_anthropic(env: Dict[str, Any], requested_model: str) -> Dict[str, Any]:
    text = env.get("result") or ""
    usage_raw = env.get("usage") or {}
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": requested_model,
        "stop_reason": env.get("stop_reason") or "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage_raw.get("input_tokens", 0) or 0),
            "output_tokens": int(usage_raw.get("output_tokens", 0) or 0),
            "cache_creation_input_tokens": int(
                usage_raw.get("cache_creation_input_tokens", 0) or 0
            ),
            "cache_read_input_tokens": int(
                usage_raw.get("cache_read_input_tokens", 0) or 0
            ),
        },
    }


# ---- routing ----

def _run_claude_backend(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    system = _system_to_text(req.system)
    with _extract_media_blocks(req.messages) as (msgs, attachments):
        prompt = _flatten_messages(msgs)
        try:
            return call_claude(
                # Use resolved display_name so version-free aliases
                # (e.g. `claude_haiku`) hit the right CLI model.
                prompt, model=model.display_name, system=system,
                attachments=attachments or None,
            )
        except ClaudeCLIError as e:
            raise HTTPException(status_code=502, detail=str(e))


def _run_gemini_backend(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    system = _system_to_text(req.system)
    with _extract_media_blocks(req.messages) as (msgs, attachments):
        prompt = _flatten_messages(msgs)
        try:
            return call_gemini(
                prompt, model=model.display_name, system=system,
                attachments=attachments or None,
            )
        except GeminiCLIError as e:
            raise HTTPException(status_code=502, detail=str(e))


def _run_openai_backend(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    if not model.url:
        raise HTTPException(status_code=500, detail=f"model {model.id} has no url")
    if any(
        isinstance(m.content, list)
        and any(b.type in ("image", "document") for b in m.content)
        for m in req.messages
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"backend {model.id!r} ({model.display_name}) is text-only. "
                "Route image/document requests to a claude-* or gemini-* "
                "model instead."
            ),
        )
    messages = anthropic_to_openai_messages(
        [m.model_dump() for m in req.messages],
        _system_to_text(req.system),
    )
    try:
        raw = call_openai_chat(
            model.url,
            model=model.display_name,
            messages=messages,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )
    except UpstreamError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return openai_to_anthropic_envelope(raw)


# ---- FastAPI app ----

app = FastAPI(title="Local LLM Hub", version="0.3.0")
# Observability middleware records every /v1/messages + /v1/chat/completions
# call into an in-memory ring read by the admin webapp's Hub tab. Volatile
# by design; the durable telemetry layer is the OTel + Langfuse stack
# bootstrapped by init_otel() above.
app.add_middleware(ObservatoryMiddleware)


# Bearer-token gate on the parent app. Loopback callers bypass; non-
# loopback callers must present the token (or be in the configured
# extra_allowlist). The /admin sub-app has its own copy of this
# middleware — its prefix is exempted here so a single auth boundary
# governs the whole process.
def _hub_get_token() -> str:
    """Resolve the bearer token from config/webapp_config.json on every
    check so the user can edit it without restarting the hub."""
    try:
        from .webapp_config import load_webapp_config
        return getattr(load_webapp_config(), "auth_token", "") or ""
    except Exception:  # noqa: BLE001
        return ""


from app_web.middleware import ParentBearerTokenMiddleware  # noqa: E402

app.add_middleware(ParentBearerTokenMiddleware, get_token=_hub_get_token)

# OTel ASGI instrumentation — added before the X-Trace-Id outer middleware
# so the OTel layer creates spans first; the X-Trace-Id wrapper then sees
# a live span context and can echo its trace ID to the client.
instrument_fastapi_app(app)

# X-Trace-Id contract — accept client-supplied UUID4 / hex in, always
# emit the current span's trace ID out. Pure-ASGI middleware; added last
# so it sits OUTERMOST.
app.add_middleware(TraceIdHeaderMiddleware)


# Expose the same WebappConfig to the parent app so the middleware can
# read ``extra_allowlist`` without re-loading on every request. Note the
# token itself is *not* cached — we always re-read so the user can
# rotate without restarting.
try:
    from .webapp_config import load_webapp_config as _load_wcfg
    app.state.webapp_config = _load_wcfg()
except Exception as _exc:  # noqa: BLE001
    logger.warning("⚠️ could not load webapp_config: %s", _exc)


@app.on_event("shutdown")
async def _stop_backend_children() -> None:
    """Tear down every model subprocess the hub spawned.

    The hub owns its backend children (since the tray drives them via
    the admin API). Without this, a clean ``CTRL+C`` would leave
    orphan ``llama-server`` / ``whisper-server`` processes holding
    their ports until the user logged out.

    Exception: on an admin **restart** the children must survive so the
    respawned hub re-adopts them (``inherit_running_backends``). The
    restart endpoint sets ``backend_process.restart_pending()`` before
    signalling shutdown; we honour it by skipping teardown.
    """
    from . import backend_process as bp

    if bp.restart_pending():
        survivors = list(bp.running_backends().keys())
        logger.info(
            "shutdown: restart in progress — leaving %d backend(s) running for adoption: %s",
            len(survivors), survivors,
        )
        return

    for model_id in list(bp.running_backends().keys()):
        try:
            ok, msg = bp.stop(model_id)
            logger.info("shutdown: stop %s -> %s %s", model_id, ok, msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("shutdown: stop %s raised: %s", model_id, exc)


@app.on_event("startup")
async def _wire_observatory_loop() -> None:
    """Capture the running event loop so the synchronous middleware can
    fan out SSE events from non-async callers."""
    import asyncio as _asyncio

    loop = _asyncio.get_running_loop()
    OBS.attach_loop(loop)
    HUB_LOG.attach_loop(loop)
    # Start the resource sampler. 2s tick × 150 samples = 5 min ring.
    loop.create_task(_resource_sampler())

    # Inherit any backend process left running on one of our ports by a
    # previous hub instance. Without this, every hub restart shows the
    # surviving model backends as "adopted" rather than "running".
    try:
        from . import backend_process as bp
        inherited = await _asyncio.to_thread(bp.inherit_running_backends)
        if inherited:
            logger.info("📎 Inherited %d running backend(s) from a previous hub", inherited)
    except Exception as exc:  # noqa: BLE001
        logger.warning("inherit_running_backends failed: %s", exc)


async def _resource_sampler() -> None:
    """Background task that samples RAM + GPU usage every 2 s."""
    import asyncio as _asyncio

    from . import system_stats
    from .hub_observability import StatSample

    while True:
        try:
            ram = system_stats.ram_stats()
            gpus = system_stats.gpu_stats()
            gpu0_vram = None
            gpu0_util = None
            if gpus:
                first = gpus[0]
                gpu0_vram = first.get("vram_percent")
                gpu0_util = first.get("util_percent")
            OBS.record_stat(
                StatSample(
                    ts=time.time(),
                    ram_percent=float(ram.get("percent", 0.0)),
                    gpu0_vram_percent=gpu0_vram,
                    gpu0_util_percent=gpu0_util,
                )
            )
        except Exception:  # noqa: BLE001 — sampler must not die
            pass
        await _asyncio.sleep(2.0)


# Mount the admin sub-app at /admin. Done at import time so a fresh
# uvicorn workers picks it up; the sub-app has its own bearer-token
# middleware, separate from the parent hub.
def _mount_admin() -> None:
    # Guard against double-mount when the module is imported twice (e.g.
    # `python -m src.server` loads us as ``__main__`` and uvicorn then
    # re-imports as ``src.server`` to resolve the ``src.server:app``
    # spec).
    if any(getattr(r, "name", None) == "admin" for r in app.routes):
        return
    try:
        from app_web import create_app as _create_admin
        admin_app = _create_admin()
        admin_app.state.parent = app
        app.mount("/admin", admin_app, name="admin")
        logger.info("ℹ️ /admin sub-app mounted")
    except Exception as exc:  # noqa: BLE001
        logger.error("⚠️ /admin sub-app failed to mount: %s", exc)


_mount_admin()


@app.get("/", include_in_schema=False)
def root() -> Response:
    # Old landing page is gone — / now redirects to the admin webapp.
    return RedirectResponse(url="/admin/", status_code=307)


@app.get("/info", include_in_schema=False)
def info() -> Dict[str, Any]:
    return {
        "name": "Local LLM Hub",
        "version": app.version,
        "description": "Multi-model hub: Anthropic-shape + OpenAI-shape over Claude / Gemini / Qwen / GLM.",
        "endpoints": {
            "health": "GET /health",
            "audio_health": "GET /v1/audio/health",
            "messages": "POST /v1/messages",
            "chat_completions": "POST /v1/chat/completions",
            "models": "GET /v1/models",
            "docs": "GET /docs",
        },
        "models": sorted({m.display_name for m in enabled_models()}),
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
def list_models() -> Dict[str, Any]:
    data = []
    for m in enabled_models():
        for name in m.all_names:
            data.append({
                "id": name,
                "object": "model",
                "owned_by": m.backend,
                "backend": m.backend,
            })
    return {"object": "list", "data": data}


def _reject_non_chat_backend(model: Model, requested_name: str) -> Optional[HTTPException]:
    """Return the 400 to raise when a chat route is hit with an ASR/TTS backend.

    The whisper and tts backends don't serve chat completions; both chat
    routes (/v1/messages and /v1/chat/completions) reject them with the
    same backend-specific "POST to the right audio endpoint instead" 400.
    Returns ``None`` for any chat-capable backend so the caller can fall
    through to its normal handling.
    """
    if model.backend == "whisper":
        return HTTPException(
            status_code=400,
            detail=(
                f"{requested_name!r} is an ASR backend, not a chat model. "
                f"POST audio to http://127.0.0.1:{model.port}/v1/audio/transcriptions instead."
            ),
        )
    if model.backend == "tts":
        return HTTPException(
            status_code=400,
            detail=(
                f"{requested_name!r} is a TTS backend, not a chat model. "
                f"POST text to http://127.0.0.1:{model.port}/v1/audio/speech instead."
            ),
        )
    return None


@app.post("/v1/messages")
def messages(req: MessagesRequest, request: Request) -> JSONResponse:
    if req.stream:
        logger.warning("stream=true requested - returning non-streaming response")

    model = _resolve(req.model)
    ctx = getattr(request.state, "obs_ctx", None)
    if ctx is not None:
        ctx.backend = model.backend
    logger.info("/v1/messages model=%s backend=%s", req.model, model.backend)

    client_id = _client_id_from(request)
    span = _current_otel_span()
    set_genai_request_attrs(
        span,
        model=req.model,
        backend=model.backend,
        operation="chat",
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        client_id=client_id,
    )
    _stash_trace_id_on_ctx(ctx, span)

    error_type = ""
    start_ns = time.monotonic_ns()
    try:
        if model.backend == "claude":
            env = _run_claude_backend(model, req)
        elif model.backend == "gemini":
            env = _run_gemini_backend(model, req)
        elif model.backend == "openai":
            env = _run_openai_backend(model, req)
        elif (reject := _reject_non_chat_backend(model, req.model)) is not None:
            raise reject
        else:
            raise HTTPException(status_code=500, detail=f"unknown backend {model.backend!r}")
    except HTTPException as exc:
        error_type = f"http_{exc.status_code}"
        record_genai_metrics(
            model=req.model, backend=model.backend, route="/v1/messages",
            client_id=client_id, duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
            error_type=error_type,
        )
        raise

    payload = _envelope_to_anthropic(env, req.model)
    u = payload["usage"]
    if ctx is not None:
        ctx.in_tok = int(u["input_tokens"])
        ctx.out_tok = int(u["output_tokens"])
        ctx.cache_read_tok = int(u["cache_read_input_tokens"])
        ctx.cache_write_tok = int(u["cache_creation_input_tokens"])
        ctx.stop_reason = str(payload.get("stop_reason") or "")
    set_genai_response_attrs(
        span,
        input_tokens=int(u["input_tokens"]),
        output_tokens=int(u["output_tokens"]),
        finish_reason=str(payload.get("stop_reason") or ""),
        response_id=str(payload.get("id") or ""),
    )
    # Attach prompt/completion bodies for Langfuse inspection. Prompt is
    # the flattened text representation we actually sent upstream; for
    # multi-turn this captures the full conversation. Completion is the
    # final assistant text.
    try:
        prompt_preview = _flatten_messages(req.messages)
    except Exception:  # noqa: BLE001
        prompt_preview = ""
    completion_preview = payload["content"][0].get("text", "") if payload.get("content") else ""
    set_genai_payload(span, prompt_preview, completion_preview)
    record_genai_metrics(
        model=req.model, backend=model.backend, route="/v1/messages",
        client_id=client_id, duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
        input_tokens=int(u["input_tokens"]), output_tokens=int(u["output_tokens"]),
    )
    logger.info(
        "<- in=%d out=%d (cache_r=%d cache_w=%d) stop=%s backend=%s",
        u["input_tokens"], u["output_tokens"],
        u["cache_read_input_tokens"], u["cache_creation_input_tokens"],
        payload["stop_reason"], model.backend,
    )
    return JSONResponse(payload)


# ---- OpenAI-shape endpoint ----

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    stream: bool = False
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    response_format: Optional[Dict[str, Any]] = None
    chat_template_kwargs: Optional[Dict[str, Any]] = None


def _wrap_as_openai(text: str, *, model_name: str, in_toks: int, out_toks: int, finish: str = "stop") -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": finish,
        }],
        "usage": {
            "prompt_tokens": in_toks,
            "completion_tokens": out_toks,
            "total_tokens": in_toks + out_toks,
        },
    }


def _stream_openai_passthrough(
    model: Model,
    req: "ChatCompletionRequest",
    *,
    span=None,
    client_id: str = "",
    start_ns: Optional[int] = None,
) -> StreamingResponse:
    """Proxy llama-server SSE through the hub, stripping ``<think>`` blocks.

    The upstream already speaks OpenAI-compatible SSE. We re-emit each
    line verbatim except ``data:`` frames whose JSON payload we mutate
    to fold ``reasoning_content`` and remove ``<think>...</think>``
    spans (using a per-stream :class:`ThinkStripper` so a tag split
    across chunks is still recognised).

    Telemetry: the generator records ``first_token`` / ``last_token``
    span events to expose time-to-first-token and tokens-per-second on
    the active span, and updates the GenAI metrics on stream close.
    """
    if not model.url:
        raise HTTPException(status_code=500, detail="model has no url")
    # Seed from the model's server-side inject_extra (e.g. the no-think alias's
    # chat_template_kwargs), then layer caller-sent fields on top so the caller
    # always wins.
    extra: Dict[str, Any] = dict(model.inject_extra or {})
    if req.tools is not None:
        extra["tools"] = req.tools
    if req.tool_choice is not None:
        extra["tool_choice"] = req.tool_choice
    if req.response_format is not None:
        extra["response_format"] = req.response_format
    if req.chat_template_kwargs is not None:
        extra["chat_template_kwargs"] = req.chat_template_kwargs

    if start_ns is None:
        start_ns = time.monotonic_ns()

    def event_stream() -> Any:
        import json as _json

        first_token_ns: Optional[int] = None
        chunk_count = 0
        usage_in = 0
        usage_out = 0
        error_type = ""
        try:
            raw = call_openai_chat_stream(
                model.url,
                model=model.display_name,
                messages=req.messages,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                extra=extra or None,
            )
            for cleaned in iter_cleaned_sse(raw):
                if cleaned.startswith("data:"):
                    payload = cleaned[len("data:"):].strip()
                    if payload and payload != "[DONE]":
                        try:
                            obj = _json.loads(payload)
                            # Detect first non-empty content delta to record TTFT.
                            if first_token_ns is None:
                                delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                                if delta.get("content"):
                                    first_token_ns = time.monotonic_ns()
                                    if span is not None and hasattr(span, "add_event"):
                                        with _safe_span("first_token"):
                                            ttft_ms = (first_token_ns - start_ns) / 1e6
                                            span.add_event(
                                                "first_token",
                                                attributes={"latency_ms": ttft_ms},
                                            )
                                            span.set_attribute(
                                                "gen_ai.response.time_to_first_token_ms",
                                                ttft_ms,
                                            )
                            # Parse usage on every frame — llama-server emits the
                            # usage chunk after content, so it arrives after
                            # first_token_ns is already set.
                            u = obj.get("usage") or {}
                            usage_in = max(usage_in, int(u.get("prompt_tokens", 0) or 0))
                            usage_out = max(usage_out, int(u.get("completion_tokens", 0) or 0))
                            chunk_count += 1
                        except Exception:  # noqa: BLE001
                            pass
                yield cleaned + "\n"
            # SSE record terminator after the final line. llama-server
            # already sends ``data: [DONE]``; the trailing blank line
            # closes the last event for strict SSE parsers.
            yield "\n"

            # Stream finished cleanly — close out telemetry.
            last_ns = time.monotonic_ns()
            if span is not None and hasattr(span, "add_event"):
                with _safe_span("last_token"):
                    span.add_event(
                        "last_token",
                        attributes={"latency_ms": (last_ns - start_ns) / 1e6},
                    )
                    if first_token_ns is not None and usage_out > 0:
                        seconds = max(1e-6, (last_ns - first_token_ns) / 1e9)
                        span.set_attribute(
                            "gen_ai.response.tokens_per_second",
                            usage_out / seconds,
                        )
                    set_genai_response_attrs(
                        span, input_tokens=usage_in, output_tokens=usage_out,
                    )
        except UpstreamError as e:
            error_type = "upstream_http_error"
            logger.error("upstream stream error: %s", e)
            err = {
                "error": {
                    "message": str(e),
                    "type": "upstream_error",
                    "code": "upstream_error",
                }
            }
            yield "data: " + _json.dumps(err) + "\n\n"
            yield "data: [DONE]\n\n"
        finally:
            record_genai_metrics(
                model=req.model, backend=model.backend,
                route="/v1/chat/completions", client_id=client_id,
                duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
                input_tokens=usage_in, output_tokens=usage_out,
                error_type=error_type,
            )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest, request: Request) -> Response:
    model = _resolve(req.model)
    ctx = getattr(request.state, "obs_ctx", None)
    if ctx is not None:
        ctx.backend = model.backend
    logger.info(
        "/v1/chat/completions model=%s backend=%s stream=%s",
        req.model, model.backend, req.stream,
    )

    client_id = _client_id_from(request)
    span = _current_otel_span()
    set_genai_request_attrs(
        span,
        model=req.model,
        backend=model.backend,
        operation="chat",
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        client_id=client_id,
    )
    _stash_trace_id_on_ctx(ctx, span)
    start_ns = time.monotonic_ns()

    if req.stream and model.backend == "openai":
        # The streaming response object closes the span itself once the
        # SSE generator hits [DONE]; record_genai_metrics is called from
        # inside the wrapped generator (see _stream_openai_passthrough).
        return _stream_openai_passthrough(
            model, req,
            span=span,
            client_id=client_id,
            start_ns=start_ns,
        )
    if req.stream:
        # Non-openai backends don't have an SSE source; fall back to a
        # single non-streaming response. Logged so it's visible.
        logger.warning(
            "stream=true on backend=%s - returning non-streaming response",
            model.backend,
        )

    error_type = ""
    try:
        if model.backend in ("claude", "gemini"):
            # Flatten OpenAI messages into a single prompt for the CLI path.
            sys_text: Optional[str] = None
            turns: List[str] = []
            for m in req.messages:
                role = m.get("role", "user")
                content = m.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                    )
                if role == "system":
                    sys_text = content
                elif role == "user":
                    turns.append(f"User: {content}")
                else:
                    turns.append(f"Assistant: {content}")
            prompt = "\n".join(turns) if len(turns) > 1 else (turns[0].split(": ", 1)[-1] if turns else "")
            try:
                if model.backend == "claude":
                    env = call_claude(prompt, model=model.display_name, system=sys_text)
                else:
                    env = call_gemini(prompt, model=model.display_name, system=sys_text)
            except (ClaudeCLIError, GeminiCLIError) as e:
                error_type = "upstream_cli_error"
                raise HTTPException(status_code=502, detail=str(e))
            text = env.get("result", "")
            usage = env.get("usage") or {}
            in_t = int(usage.get("input_tokens", 0) or 0)
            out_t = int(usage.get("output_tokens", 0) or 0)
            set_genai_response_attrs(
                span, input_tokens=in_t, output_tokens=out_t,
                finish_reason=str(env.get("stop_reason") or ""),
            )
            set_genai_payload(span, prompt, text)
            record_genai_metrics(
                model=req.model, backend=model.backend,
                route="/v1/chat/completions", client_id=client_id,
                duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
                input_tokens=in_t, output_tokens=out_t,
            )
            return JSONResponse(_wrap_as_openai(
                text, model_name=req.model, in_toks=in_t, out_toks=out_t,
            ))

        reject = _reject_non_chat_backend(model, req.model)
        if reject is not None:
            error_type = "http_400"
            raise reject

        if model.backend == "openai":
            if not model.url:
                error_type = "config_error"
                raise HTTPException(status_code=500, detail="model has no url")
            # Seed from inject_extra (no-think alias), then caller fields win.
            extra: Dict[str, Any] = dict(model.inject_extra or {})
            if req.tools is not None:
                extra["tools"] = req.tools
            if req.tool_choice is not None:
                extra["tool_choice"] = req.tool_choice
            if req.response_format is not None:
                extra["response_format"] = req.response_format
            if req.chat_template_kwargs is not None:
                extra["chat_template_kwargs"] = req.chat_template_kwargs
            try:
                raw = call_openai_chat(
                    model.url,
                    model=model.display_name,
                    messages=req.messages,
                    max_tokens=req.max_tokens,
                    temperature=req.temperature,
                    extra=extra or None,
                )
            except UpstreamError as e:
                error_type = "upstream_http_error"
                raise HTTPException(status_code=502, detail=str(e))
            cleaned = clean_openai_response(raw)
            usage = cleaned.get("usage") or {}
            in_t = int(usage.get("prompt_tokens", 0) or 0)
            out_t = int(usage.get("completion_tokens", 0) or 0)
            set_genai_response_attrs(span, input_tokens=in_t, output_tokens=out_t)
            try:
                completion_text = (cleaned.get("choices") or [{}])[0].get(
                    "message", {}
                ).get("content") or ""
            except Exception:  # noqa: BLE001
                completion_text = ""
            set_genai_payload(span, _flatten_openai_prompt(req.messages), completion_text)
            record_genai_metrics(
                model=req.model, backend=model.backend,
                route="/v1/chat/completions", client_id=client_id,
                duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
                input_tokens=in_t, output_tokens=out_t,
            )
            # Passthrough of upstream response (already OpenAI-shape), with
            # <think>...</think> stripped from message.content and
            # reasoning_content folded into content when content is empty.
            return JSONResponse(cleaned)

        error_type = "unknown_backend"
        raise HTTPException(status_code=500, detail=f"unknown backend {model.backend!r}")
    finally:
        if error_type:
            record_genai_metrics(
                model=req.model, backend=model.backend,
                route="/v1/chat/completions", client_id=client_id,
                duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
                error_type=error_type,
            )


def _flatten_openai_prompt(messages: List[Dict[str, Any]]) -> str:
    """Cheap flattener for telemetry capture only — best-effort, never raises."""
    try:
        parts: List[str] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            parts.append(f"{role}: {content}")
        return "\n".join(parts)
    except Exception:  # noqa: BLE001
        return ""


# ---- Image + audio routes (split into sibling modules) ----
# The /v1/images/* handlers live in server_images.py and the /v1/audio/*
# proxy in server_audio.py; both are plain APIRouters mounted here so the
# admin sub-app's auth boundary and the observability middleware still cover
# them. See those modules for the handler bodies.
app.include_router(_images_router)
app.include_router(_audio_router)


def main() -> None:
    import uvicorn
    uvicorn.run("src.server:app", host=hub_bind_host(), port=hub_port(), reload=False)


if __name__ == "__main__":
    main()
