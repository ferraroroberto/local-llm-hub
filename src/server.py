"""Local multi-model hub: Anthropic-compatible and OpenAI-compatible endpoints.

Routes each request by the `model` field to one of the backends in
`config/models.yaml`:

- claude-*                -> `claude -p` subprocess (Anthropic subscription)
- gemini-*                -> `gemini -p` subprocess (Google AI Pro subscription)
- qwen3.5-9b / qwen*      -> llama-server at 127.0.0.1:8081 (/v1)
- glm-4.5-air / glm*      -> llama-server at 127.0.0.1:8082 (/v1)

Two shapes exposed:
  * POST /v1/messages          - Anthropic shape (drop-in for the SDK)
  * POST /v1/chat/completions  - OpenAI shape (passthrough/translation)
  * GET  /v1/models            - union of enabled names (both shapes)

Caveats: text-only content; no tool_use round-trip on the Anthropic
shape for non-claude backends (OpenAI-shape callers get tool use
natively from llama-server). Streaming: ``/v1/chat/completions``
proxies upstream SSE through (with ``<think>`` blocks stripped for
reasoning models); ``/v1/messages`` still returns a single JSON for
``stream=true`` until the Anthropic event translation lands.
"""

from __future__ import annotations

import base64
import logging
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .claude_cli import ClaudeCLIError, call_claude
from .gemini_cli import GeminiCLIError, call_gemini
from .host_profile import hub_bind_host, hub_port
from .landing import LANDING_HTML
from .model_registry import Model, enabled_models, resolve as resolve_model
from .openai_upstream import (
    UpstreamError,
    anthropic_to_openai_messages,
    call_openai_chat,
    call_openai_chat_stream,
    clean_openai_response,
    iter_cleaned_sse,
    openai_to_anthropic_envelope,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


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
}


@contextmanager
def _extract_image_blocks(
    messages: List[Message],
) -> Iterator[Tuple[List[Message], List[Path]]]:
    """Pull image content blocks out of messages, write them to a temp dir.

    Yields ``(stripped_messages, image_paths)``. Stripped messages keep
    only text blocks so the existing flattener works unchanged. The temp
    dir and its contents are removed when the context exits, which must
    not happen until after the backend subprocess returns.

    Only Anthropic-style ``{"type": "image", "source": {"type": "base64",
    ...}}`` blocks are extracted today. ``source.type == "url"`` is
    forwarded as a text reference to the URL since neither CLI fetches
    remote URLs on our behalf — image-by-URL needs `httpx.get` first,
    which we can add later if a caller actually needs it.
    """
    image_paths: List[Path] = []
    stripped: List[Message] = []
    has_images = any(
        isinstance(m.content, list)
        and any(b.type == "image" for b in m.content)
        for m in messages
    )

    if not has_images:
        # Fast path — no temp dir at all when there's nothing to extract.
        yield messages, []
        return

    with tempfile.TemporaryDirectory(prefix="hub-img-") as td:
        td_path = Path(td)
        for msg in messages:
            if isinstance(msg.content, str):
                stripped.append(msg)
                continue
            kept: List[ContentBlock] = []
            for block in msg.content:
                if block.type != "image" or not block.source:
                    kept.append(block)
                    continue
                src = block.source
                stype = src.get("type")
                if stype == "base64":
                    data_b64 = src.get("data") or ""
                    media = src.get("media_type", "image/png")
                    ext = _EXT_BY_MEDIA_TYPE.get(media, "bin")
                    fname = f"img_{len(image_paths)}.{ext}"
                    fpath = td_path / fname
                    try:
                        fpath.write_bytes(base64.b64decode(data_b64))
                    except Exception as e:
                        raise HTTPException(
                            status_code=400,
                            detail=f"bad image block: {e}",
                        )
                    image_paths.append(fpath)
                elif stype == "url":
                    url = src.get("url", "")
                    kept.append(ContentBlock(type="text", text=f"[image url: {url}]"))
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"unsupported image source.type {stype!r}",
                    )
            # Keep at least an empty text block so flatteners don't crash.
            if not kept:
                kept = [ContentBlock(type="text", text="")]
            stripped.append(Message(role=msg.role, content=kept))
        yield stripped, image_paths


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

def _resolve(model_name: str) -> Model:
    m = resolve_model(model_name)
    if m is None:
        known = [m.display_name for m in enabled_models()]
        raise HTTPException(
            status_code=400,
            detail=f"unknown model {model_name!r}. available on this host: {known}",
        )
    return m


def _run_claude_backend(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    system = _system_to_text(req.system)
    with _extract_image_blocks(req.messages) as (msgs, images):
        prompt = _flatten_messages(msgs)
        try:
            return call_claude(
                # Use resolved display_name so version-free aliases
                # (e.g. `claude_haiku`) hit the right CLI model.
                prompt, model=model.display_name, system=system,
                images=images or None,
            )
        except ClaudeCLIError as e:
            raise HTTPException(status_code=502, detail=str(e))


def _run_gemini_backend(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    system = _system_to_text(req.system)
    with _extract_image_blocks(req.messages) as (msgs, images):
        prompt = _flatten_messages(msgs)
        try:
            return call_gemini(
                prompt, model=model.display_name, system=system,
                images=images or None,
            )
        except GeminiCLIError as e:
            raise HTTPException(status_code=502, detail=str(e))


def _run_openai_backend(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    if not model.url:
        raise HTTPException(status_code=500, detail=f"model {model.id} has no url")
    if any(
        isinstance(m.content, list) and any(b.type == "image" for b in m.content)
        for m in req.messages
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"backend {model.id!r} ({model.display_name}) is text-only. "
                "Route image requests to a claude-* or gemini-* model instead."
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

app = FastAPI(title="Local LLM Hub", version="0.2.0")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root() -> HTMLResponse:
    return HTMLResponse(LANDING_HTML)


@app.get("/info", include_in_schema=False)
def info() -> Dict[str, Any]:
    return {
        "name": "Local LLM Hub",
        "version": app.version,
        "description": "Multi-model hub: Anthropic-shape + OpenAI-shape over Claude / Gemini / Qwen / GLM.",
        "endpoints": {
            "health": "GET /health",
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


@app.post("/v1/messages")
def messages(req: MessagesRequest) -> JSONResponse:
    if req.stream:
        logger.warning("stream=true requested - returning non-streaming response")

    model = _resolve(req.model)
    logger.info("/v1/messages model=%s backend=%s", req.model, model.backend)

    if model.backend == "claude":
        env = _run_claude_backend(model, req)
    elif model.backend == "gemini":
        env = _run_gemini_backend(model, req)
    elif model.backend == "openai":
        env = _run_openai_backend(model, req)
    elif model.backend == "whisper":
        raise HTTPException(
            status_code=400,
            detail=(
                f"{req.model!r} is an ASR backend, not a chat model. "
                f"POST audio to http://127.0.0.1:{model.port}/v1/audio/transcriptions instead."
            ),
        )
    else:
        raise HTTPException(status_code=500, detail=f"unknown backend {model.backend!r}")

    payload = _envelope_to_anthropic(env, req.model)
    u = payload["usage"]
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


def _stream_openai_passthrough(model: Model, req: "ChatCompletionRequest") -> StreamingResponse:
    """Proxy llama-server SSE through the hub, stripping ``<think>`` blocks.

    The upstream already speaks OpenAI-compatible SSE. We re-emit each
    line verbatim except ``data:`` frames whose JSON payload we mutate
    to fold ``reasoning_content`` and remove ``<think>...</think>``
    spans (using a per-stream :class:`ThinkStripper` so a tag split
    across chunks is still recognised).
    """
    if not model.url:
        raise HTTPException(status_code=500, detail="model has no url")
    extra: Dict[str, Any] = {}
    if req.tools is not None:
        extra["tools"] = req.tools
    if req.tool_choice is not None:
        extra["tool_choice"] = req.tool_choice

    def event_stream() -> Any:
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
                yield cleaned + "\n"
            # SSE record terminator after the final line. llama-server
            # already sends ``data: [DONE]``; the trailing blank line
            # closes the last event for strict SSE parsers.
            yield "\n"
        except UpstreamError as e:
            logger.error("upstream stream error: %s", e)
            err = {
                "error": {
                    "message": str(e),
                    "type": "upstream_error",
                    "code": "upstream_error",
                }
            }
            import json as _json
            yield "data: " + _json.dumps(err) + "\n\n"
            yield "data: [DONE]\n\n"

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
def chat_completions(req: ChatCompletionRequest) -> Response:
    model = _resolve(req.model)
    logger.info(
        "/v1/chat/completions model=%s backend=%s stream=%s",
        req.model, model.backend, req.stream,
    )

    if req.stream and model.backend == "openai":
        return _stream_openai_passthrough(model, req)
    if req.stream:
        # Non-openai backends don't have an SSE source; fall back to a
        # single non-streaming response. Logged so it's visible.
        logger.warning(
            "stream=true on backend=%s - returning non-streaming response",
            model.backend,
        )

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
            raise HTTPException(status_code=502, detail=str(e))
        text = env.get("result", "")
        usage = env.get("usage") or {}
        return JSONResponse(_wrap_as_openai(
            text, model_name=req.model,
            in_toks=int(usage.get("input_tokens", 0) or 0),
            out_toks=int(usage.get("output_tokens", 0) or 0),
        ))

    if model.backend == "whisper":
        raise HTTPException(
            status_code=400,
            detail=(
                f"{req.model!r} is an ASR backend, not a chat model. "
                f"POST audio to http://127.0.0.1:{model.port}/v1/audio/transcriptions instead."
            ),
        )

    if model.backend == "openai":
        if not model.url:
            raise HTTPException(status_code=500, detail="model has no url")
        extra: Dict[str, Any] = {}
        if req.tools is not None:
            extra["tools"] = req.tools
        if req.tool_choice is not None:
            extra["tool_choice"] = req.tool_choice
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
            raise HTTPException(status_code=502, detail=str(e))
        # Passthrough of upstream response (already OpenAI-shape), with
        # <think>...</think> stripped from message.content and
        # reasoning_content folded into content when content is empty.
        return JSONResponse(clean_openai_response(raw))

    raise HTTPException(status_code=500, detail=f"unknown backend {model.backend!r}")


def main() -> None:
    import uvicorn
    uvicorn.run("src.server:app", host=hub_bind_host(), port=hub_port(), reload=False)


if __name__ == "__main__":
    main()
