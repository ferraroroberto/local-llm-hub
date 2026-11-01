"""Chat-shape translation: request/response schemas, media-block extraction,
prompt flattening, and per-backend dispatch shared by the ``/v1/messages`` and
``/v1/chat/completions`` routes (``routes/anthropic_messages.py`` and
``routes/openai_chat.py``). The Anthropic SSE event
adapters live in ``translation/anthropic_sse.py`` and are re-exported here.

Holds the Pydantic schemas, the Anthropic content-block media extractor, the
multi-turn prompt flattener, and the three per-backend dispatchers
(``_run_claude_backend`` / ``_run_gemini_backend`` / ``_run_openai_backend``)
— out of the route modules, same as the audio/images/lifecycle concerns
(``audio/server_audio_asr.py``/``audio/server_audio_tts.py``,
``images/server_images.py``, ``server_lifecycle.py``). The route modules keep
the endpoint handlers themselves (including the OpenAI SSE passthrough
generator); ``server.py`` only includes their routers and assembles the
FastAPI app/middleware.

A leaf module with no dependency on ``server.py``'s ``app`` — mirrors
``server_common.py``'s reason for existing, so route modules (and this one)
can import each other without a circular import back into ``server.py``.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Type, Union

from fastapi import HTTPException
from pydantic import BaseModel

from .claude_cli import ClaudeCLIError, call_claude
from .gemini_cli import GeminiCLIError, call_gemini
from .hub_observability import error_class_of, tag_error
from .model_registry import Model
from .openai_upstream import (
    UpstreamError,
    anthropic_to_openai_messages,
    anthropic_tool_choice_to_openai,
    anthropic_tools_to_openai,
    call_openai_chat,
    openai_to_anthropic_envelope,
)
from .fleet.remote_proxy import remote_auth_headers, remote_base_url
# The Anthropic SSE adapters live in their own module; re-exported here so
# every importer keeps the chat_translation path.
from .translation.anthropic_sse import (  # noqa: F401
    AnthropicStreamState,
    anthropic_stream_error,
    iter_buffered_anthropic_sse,
    iter_claude_anthropic_sse,
    iter_openai_anthropic_sse,
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
    # tool_use block (assistant turn): {"type": "tool_use", "id": "toolu_...",
    # "name": "get_weather", "input": {...}}.
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    # tool_result block (user turn): {"type": "tool_result", "tool_use_id":
    # "toolu_...", "content": str | [block, ...], "is_error": bool}.
    tool_use_id: Optional[str] = None
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    is_error: Optional[bool] = None


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
    # Anthropic tool definitions ({"name", "description", "input_schema"}) and
    # tool_choice ({"type": "auto"|"any"|"none"|"tool", "name": ...}). Served
    # by the local `openai` backends; refused on the CLI backends.
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    # Sampling / stop controls. Forwarded to the local `openai`
    # backends; on the CLI backends `top_p` / `top_k` are accepted no-ops
    # (like `temperature`) and `stop_sequences` is refused.
    stop_sequences: Optional[List[str]] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    # Extended thinking ({"type": "enabled", "budget_tokens": N}). The hub
    # emits no thinking blocks, so only {"type": "disabled"} is accepted.
    thinking: Optional[Dict[str, Any]] = None


# Content-block types that only mean anything on a tool-capable backend.
_TOOL_BLOCK_TYPES = ("tool_use", "tool_result")


def openai_tool_params(req: MessagesRequest) -> Dict[str, Any]:
    """Extra upstream parameters carrying this request's tool definitions.

    Empty when the caller sent no ``tools``, so a plain chat request reaches
    llama-server byte-identical to before. Translation errors are the
    caller's (a malformed tool definition), so they surface as 400 rather
    than ``UpstreamError``'s 502.
    """
    if not req.tools:
        return {}
    try:
        extra: Dict[str, Any] = {"tools": anthropic_tools_to_openai(req.tools)}
        choice = anthropic_tool_choice_to_openai(req.tool_choice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if choice is not None:
        extra["tool_choice"] = choice
    return extra


def openai_backend_extra(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    """Upstream payload overlay for a ``/v1/messages`` call to an ``openai`` backend.

    Seeds from the row's server-side ``inject_extra`` (e.g. the no-think
    alias's ``chat_template_kwargs``), then layers this request's tool params
    on top so the caller wins on a key collision. The single place both the
    buffered and streaming paths build it (#567 — the buffered path used to
    drop the overlay).
    """
    extra: Dict[str, Any] = dict(model.inject_extra or {})
    extra.update(openai_tool_params(req))
    extra.update(openai_sampling_params(req))
    return extra


def openai_sampling_params(req: MessagesRequest) -> Dict[str, Any]:
    """Extra upstream parameters carrying this request's stop/sampling controls.

    ``stop_sequences`` maps to OpenAI's ``stop``; llama-server honors ``stop``,
    ``top_p`` and ``top_k`` on ``/v1/chat/completions``. Unset fields are left
    out so a request without them reaches the backend unchanged.
    """
    extra: Dict[str, Any] = {}
    if req.stop_sequences:
        extra["stop"] = list(req.stop_sequences)
    if req.top_p is not None:
        extra["top_p"] = float(req.top_p)
    if req.top_k is not None:
        extra["top_k"] = int(req.top_k)
    return extra


def reject_unservable_request(model: Model, req: MessagesRequest) -> None:
    """400 when a request asks for something its backend cannot honor.

    Called before dispatch on every ``/v1/messages`` path (buffered and before
    a stream begins), so the refusal is a real 400, never a dropped parameter
    that yields a well-formed wrong answer (#474, #552, #607).
    """
    if req.thinking is not None and req.thinking.get("type") != "disabled":
        raise HTTPException(
            status_code=400,
            detail=(
                "extended thinking is not supported: the hub returns no "
                "thinking blocks on any backend. Omit 'thinking' or send "
                "{\"type\": \"disabled\"}."
            ),
        )
    if model.backend not in ("claude", "gemini"):
        return
    _reject_tools_on_cli_backend(model, req)
    if req.stop_sequences:
        raise HTTPException(
            status_code=400,
            detail=(
                f"backend {model.id!r} ({model.display_name}) cannot honor "
                "'stop_sequences': the CLI backends expose no stop control. "
                "Route the request to a local openai-backend model instead."
            ),
        )
    if req.top_p is not None or req.top_k is not None:
        logger.info(
            "ℹ️ /v1/messages backend=%s ignores top_p/top_k (no CLI sampling control)",
            model.backend,
        )


def _reject_tools_on_cli_backend(model: Model, req: MessagesRequest) -> None:
    """400 when tool use is asked of a backend that cannot serve it.

    The ``claude`` / ``gemini`` dispatch flattens a conversation into one text
    prompt (``_flatten_messages``), so ``tools`` and ``tool_use`` /
    ``tool_result`` blocks have nowhere to go. Dropping them silently would
    answer in prose a caller that asked for a tool call — the well-formed
    wrong answer #474 refused for non-text parts on the OpenAI shape.
    """
    if req.tools:
        unsupported = "a 'tools' parameter"
    elif any(
        isinstance(m.content, list)
        and any(b.type in _TOOL_BLOCK_TYPES for b in m.content)
        for m in req.messages
    ):
        unsupported = "tool_use / tool_result content blocks"
    else:
        return
    raise HTTPException(
        status_code=400,
        detail=(
            f"backend {model.id!r} ({model.display_name}) cannot serve "
            f"{unsupported}: the CLI backends flatten a conversation into a "
            "single text prompt. Route tool-use requests to a local "
            "openai-backend model instead."
        ),
    )


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
                    supplied_suffix = Path(str(src.get("filename") or "")).suffix
                    supplied_ext = supplied_suffix.lstrip(".").lower()
                    ext = (
                        supplied_ext
                        if re.fullmatch(r"[a-z0-9]{1,12}", supplied_ext)
                        else _EXT_BY_MEDIA_TYPE.get(media, "bin")
                    )
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
    """Flatten multi-turn into one prompt for the claude/gemini CLI dispatch.

    Shared by both the Anthropic-shape ``/v1/messages`` route (via
    ``_run_claude_backend``/``_run_gemini_backend``) and the OpenAI-shape
    ``/v1/chat/completions`` route (via ``_openai_messages_to_anthropic``
    below) — one prompt scaffold, so a format change applied here reaches
    both routes instead of only whichever one happened to get edited.
    """
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


def _openai_messages_to_anthropic(
    messages: List[Dict[str, Any]],
    model_label: Optional[str] = None,
) -> Tuple[List[Message], Optional[str]]:
    """Normalize OpenAI-shape dict messages into Anthropic-shape ``Message``
    objects plus an extracted system prompt, so ``/v1/chat/completions`` can
    reuse ``_flatten_messages`` instead of hand-rolling its own prompt
    scaffold (issue #195 — the two routes previously diverged silently).

    Inline OpenAI ``file`` parts become Anthropic-style ``document`` blocks so
    the caller can reuse ``_extract_media_blocks`` and the existing CLI
    attachment path (#554). Other non-text parts (``image_url``,
    ``input_audio``, …) are **refused with a 400** rather than dropped (issue
    #474): silently keeping only text would return a well-formed answer to a
    request the caller never made.
    """
    sys_text: Optional[str] = None
    turns: List[Message] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            normalized: List[ContentBlock] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    normalized.append(
                        ContentBlock(type="text", text=part.get("text", ""))
                    )
                elif isinstance(part, dict) and part.get("type") == "file":
                    normalized.append(_openai_file_to_document(part, model_label))

            unsupported = sorted({
                str(p.get("type") or "unknown") if isinstance(p, dict) else type(p).__name__
                for p in content
                if not (
                    isinstance(p, dict)
                    and p.get("type") in ("text", "file")
                )
            })
            if unsupported:
                who = f"{model_label!r} " if model_label else ""
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"backend {who}cannot accept {', '.join(unsupported)} content "
                        "on /v1/chat/completions — this route flattens messages to a "
                        "text prompt. Use text/file parts here or send the media input "
                        "to POST /v1/messages instead."
                    ),
                )
            content = normalized
        if role == "system":
            if isinstance(content, list):
                if any(block.type == "document" for block in content):
                    raise HTTPException(
                        status_code=400,
                        detail="file content parts are only supported in user messages",
                    )
                sys_text = _content_to_text(content)
            else:
                sys_text = content
        else:
            turns.append(Message(role=role, content=content))
    return turns, sys_text


def _openai_file_to_document(
    part: Dict[str, Any],
    model_label: Optional[str],
) -> ContentBlock:
    """Validate one Chat Completions ``file`` part and normalize it.

    ``file_id`` would require a local file-store contract and ``file_url``
    would require an authenticated fetch policy, neither of which this hub
    exposes. Inline bytes are safe to support because the existing document
    extractor owns their request-scoped lifetime and uses its own filename.
    """
    who = f"backend {model_label!r} " if model_label else "this backend "
    payload = part.get("file")
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail=f"{who}received a file content part without a file object",
        )
    if payload.get("file_id"):
        raise HTTPException(
            status_code=400,
            detail=f"{who}cannot resolve file_id: this hub has no /v1/files store",
        )
    if payload.get("file_url"):
        raise HTTPException(
            status_code=400,
            detail=f"{who}cannot fetch file_url: send inline file_data instead",
        )

    filename = payload.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise HTTPException(
            status_code=400,
            detail=f"{who}requires filename for an inline file content part",
        )
    file_data = payload.get("file_data")
    if not isinstance(file_data, str) or not file_data.strip():
        raise HTTPException(
            status_code=400,
            detail=f"{who}requires inline file_data; file_id and file_url are unsupported",
        )

    media_type = (
        mimetypes.guess_type(Path(filename).name)[0]
        or "application/octet-stream"
    )
    encoded = file_data.strip()
    if encoded.startswith("data:"):
        match = re.fullmatch(r"data:([^;,]+);base64,(.*)", encoded, flags=re.DOTALL)
        if match is None:
            raise HTTPException(
                status_code=400,
                detail=f"{who}received malformed file_data data URL",
            )
        media_type = match.group(1)
        encoded = match.group(2)

    encoded = re.sub(r"\s+", "", encoded)
    try:
        base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{who}received invalid base64 file_data: {exc}",
        )

    return ContentBlock(
        type="document",
        source={
            "type": "base64",
            "media_type": media_type,
            "data": encoded,
            # _extract_media_blocks uses only a validated suffix from this
            # untrusted name; the caller's directories/basename never reach
            # disk. This preserves useful extensions such as .docx and .py.
            "filename": Path(filename).name,
        },
    )


# ---- routing ----

def _run_cli_backend(
    model: Model,
    req: MessagesRequest,
    call_fn: Callable[..., Dict[str, Any]],
    error_cls: Type[Exception],
) -> Dict[str, Any]:
    """Dispatch a non-streaming ``/v1/messages`` request to a subscription
    CLI (``call_claude`` / ``call_gemini``); ``error_cls`` maps to a 502."""
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")
    reject_unservable_request(model, req)
    system = _system_to_text(req.system)
    with _extract_media_blocks(req.messages) as (msgs, attachments):
        prompt = _flatten_messages(msgs)
        try:
            return call_fn(
                # Use resolved display_name so version-free aliases
                # (e.g. `claude_haiku`) hit the right CLI model.
                prompt, model=model.display_name, system=system,
                attachments=attachments or None,
            )
        except error_cls as e:
            raise tag_error(HTTPException(status_code=502, detail=str(e)), error_class_of(e))


# Thin wrappers rather than ``partial`` bindings: the CLI function is looked
# up at call time, so tests that monkeypatch ``call_claude`` / ``call_gemini``
# on this module still intercept the dispatch.
def _run_claude_backend(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    return _run_cli_backend(model, req, call_claude, ClaudeCLIError)


def _run_gemini_backend(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    return _run_cli_backend(model, req, call_gemini, GeminiCLIError)


@dataclass(frozen=True)
class OpenAIUpstream:
    """Where an ``openai``-backend chat call is sent (see
    :func:`resolve_openai_upstream`)."""

    remote: Optional[str]
    base_url: str
    model_name: str
    headers: Optional[Dict[str, str]]


def resolve_openai_upstream(model: Model) -> OpenAIUpstream:
    """Resolve the upstream for an ``openai``-backend chat call.

    A model owned by a peer host goes to that hub's ``/v1``, addressed by
    registry id (the peer resolves it again) and carrying its bearer token if
    one is configured; a locally-served one goes straight to its llama-server
    ``url``, addressed by display name. Shared by all four dispatch paths
    (``/v1/messages`` and ``/v1/chat/completions``, buffered and streaming)
    so the remote-vs-local rule has one home. 500 when neither resolves.
    """
    remote = remote_base_url(model)
    base_url = f"{remote}/v1" if remote else model.url
    if not base_url:
        raise HTTPException(status_code=500, detail=f"model {model.id} has no url")
    return OpenAIUpstream(
        remote=remote,
        base_url=base_url,
        model_name=model.id if remote else model.display_name,
        headers=(remote_auth_headers(model) or None) if remote else None,
    )


def call_openai_upstream(
    model: Model,
    upstream: OpenAIUpstream,
    messages: List[Dict[str, Any]],
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Send one *buffered* chat call to an already-resolved OpenAI upstream.

    Owns the pair both buffered dispatch paths need identically: the
    on-demand idle-tracking context (#422, so a locally-served ``on_demand``
    row isn't unloaded mid-request) and the ``UpstreamError`` -> 502 mapping.
    Used by ``_run_openai_backend`` (``/v1/messages``) and ``chat_completions``
    (``/v1/chat/completions``).

    The two *streaming* paths deliberately keep their own handling: by the
    time an upstream failure surfaces there the response headers are already
    sent, so it becomes an in-stream SSE error event rather than a 502.
    """
    from . import on_demand as _on_demand
    try:
        with _on_demand.tracking(model, upstream.remote):
            return call_openai_chat(
                upstream.base_url,
                model=upstream.model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                extra=extra or None,
                headers=upstream.headers,
            )
    except UpstreamError as e:
        raise tag_error(HTTPException(status_code=502, detail=str(e)), error_class_of(e))


def prepare_openai_messages_call(
    model: Model, req: MessagesRequest
) -> Tuple[OpenAIUpstream, List[Dict[str, Any]], Dict[str, Any]]:
    """Validate a ``/v1/messages`` request for an ``openai`` backend, then ready it.

    The one setup both the buffered (``_run_openai_backend``) and streaming
    (``routes.anthropic_messages._stream_anthropic_response``) paths share. Every 400 (unservable
    request, malformed tool definition, image/document on a text-only row) is
    raised *before* the on-demand spin-up, so a bad request never cold-starts
    a model to discover it. Returns ``(upstream, openai_messages, extra)``.
    """
    reject_unservable_request(model, req)
    extra = openai_backend_extra(model, req)
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
    # On-demand lifecycle: a cold ``startup: on_demand`` local backend
    # is spawned here and the request blocks until it answers (503 on load
    # failure) — same hook the OpenAI-shape route applies in routes/openai_chat.py.
    from .server_common import ensure_backend_ready_or_503
    ensure_backend_ready_or_503(model)
    upstream = resolve_openai_upstream(model)
    messages = anthropic_to_openai_messages(
        [m.model_dump() for m in req.messages],
        _system_to_text(req.system),
    )
    return upstream, messages, extra


def _run_openai_backend(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    upstream, messages, extra = prepare_openai_messages_call(model, req)
    raw = call_openai_upstream(
        model,
        upstream,
        messages,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        extra=extra,
    )
    try:
        return openai_to_anthropic_envelope(raw)
    except UpstreamError as e:
        # A malformed tool call is an upstream fault, same as the 502 that
        # call_openai_upstream applies to the request itself.
        raise tag_error(HTTPException(status_code=502, detail=str(e)), error_class_of(e))
