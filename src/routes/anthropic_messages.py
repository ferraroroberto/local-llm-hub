"""Anthropic-shape chat routes: ``POST /v1/messages`` (buffered and SSE) and
``POST /v1/messages/count_tokens``.

Per-backend request translation and dispatch live in ``chat_translation.py``;
this module owns the response shape, telemetry and the streaming lifecycle.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import ExitStack, closing
from typing import Any, Dict, Iterator, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .. import on_demand as _on_demand
from ..chat_translation import (
    AnthropicStreamState,
    MessagesRequest,
    OpenAIUpstream,
    _extract_media_blocks,
    _flatten_messages,
    _run_claude_backend,
    _run_gemini_backend,
    _run_openai_backend,
    _system_to_text,
    anthropic_stream_error,
    iter_buffered_anthropic_sse,
    iter_claude_anthropic_sse,
    iter_openai_anthropic_sse,
    prepare_openai_messages_call,
    reject_unservable_request,
)
from ..claude_cli import ClaudeCLIError, call_claude_stream
from ..gemini_cli import GeminiCLIError
from ..hub_observability import (
    ERROR_CLIENT_DISCONNECT,
    ERROR_INTERNAL,
    error_class_for_status,
    error_class_of,
    stamp_ctx_failure,
)
from ..model_registry import Model
from ..observability import set_genai_payload, set_genai_request_attrs, set_genai_response_attrs
from ..openai_upstream import UpstreamError, call_openai_chat_stream, iter_cleaned_sse
from ..server_common import (
    client_id_from as _client_id_from,
    current_otel_span as _current_otel_span,
    record_first_token as _record_first_token,
    record_last_token as _record_last_token,
    record_route_metrics,
    resolve_model_or_400 as _resolve,
    stash_trace_id_on_ctx as _stash_trace_id_on_ctx,
)
from .token_counting import count_tokens as _count_tokens
from .sse_response import SSE_HEADERS, ClosingStreamingResponse, close_if_supported, reject_non_chat_backend

logger = logging.getLogger(__name__)
router = APIRouter()

_ROUTE = "/v1/messages"


def _envelope_to_anthropic(env: Dict[str, Any], requested_model: str) -> Dict[str, Any]:
    text = env.get("result") or ""
    usage_raw = env.get("usage") or {}
    # Backends that can return structured content (the openai path, which may
    # emit tool_use blocks) supply "content"; the CLI backends return
    # text only and fall back to a single text block.
    content = env.get("content") or [{"type": "text", "text": text}]
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content,
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


def _prompt_preview(req: MessagesRequest) -> str:
    """The flattened prompt for Langfuse; best-effort, never raises."""
    try:
        return _flatten_messages(req.messages)
    except Exception:  # noqa: BLE001
        return ""


def _claude_events(model: Model, req: MessagesRequest, state: AnthropicStreamState) -> Iterator[str]:
    system = _system_to_text(req.system)
    with _extract_media_blocks(req.messages) as (messages, attachments):
        prompt = _flatten_messages(messages)
        with ExitStack() as streams:
            records = call_claude_stream(
                prompt,
                model=model.display_name,
                system=system,
                attachments=attachments or None,
            )
            streams.callback(close_if_supported, records)
            events = iter_claude_anthropic_sse(records, state)
            streams.callback(close_if_supported, events)
            for event in events:
                yield event


def _openai_events(
    upstream: OpenAIUpstream,
    openai_messages: List[Dict[str, Any]],
    openai_extra: Dict[str, Any],
    req: MessagesRequest,
    state: AnthropicStreamState,
) -> Iterator[str]:
    extra = {**openai_extra, "stream_options": {"include_usage": True}}
    with ExitStack() as streams:
        raw = call_openai_chat_stream(
            upstream.base_url,
            model=upstream.model_name,
            messages=openai_messages,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            extra=extra,
            headers=upstream.headers,
        )
        streams.callback(close_if_supported, raw)
        cleaned = iter_cleaned_sse(raw)
        streams.callback(close_if_supported, cleaned)
        events = iter_openai_anthropic_sse(cleaned, state)
        streams.callback(close_if_supported, events)
        for event in events:
            yield event


def _finish_anthropic_stream(
    model: Model,
    req: MessagesRequest,
    state: AnthropicStreamState,
    *,
    ctx: Any,
    span: Any,
    client_id: str,
    start_ns: int,
    error_type: str,
) -> None:
    """Close out the ring entry, span and metrics once a stream ends."""
    if ctx is not None:
        ctx.in_tok = state.input_tokens
        ctx.out_tok = state.output_tokens
        ctx.cache_read_tok = state.cache_read_tokens
        ctx.cache_write_tok = state.cache_write_tokens
        ctx.stop_reason = state.stop_reason
    set_genai_response_attrs(
        span,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        finish_reason=state.stop_reason,
        response_id=state.message_id,
    )
    set_genai_payload(span, _prompt_preview(req), state.text)
    record_route_metrics(
        start_ns,
        model=req.model,
        backend=model.backend,
        route=_ROUTE,
        client_id=client_id,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        error_type=error_type,
    )
    logger.info(
        "<- stream in=%d out=%d (cache_r=%d cache_w=%d) stop=%s backend=%s",
        state.input_tokens,
        state.output_tokens,
        state.cache_read_tokens,
        state.cache_write_tokens,
        state.stop_reason,
        model.backend,
    )


def _stream_anthropic_response(
    model: Model,
    req: MessagesRequest,
    *,
    ctx: Any,
    span: Any,
    client_id: str,
    start_ns: int,
) -> StreamingResponse:
    """Return Anthropic Messages SSE for each supported chat backend."""
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")
    if (reject := reject_non_chat_backend(model, req.model)) is not None:
        raise reject
    # Raised here, before the StreamingResponse begins, so an unsupported
    # request fails as a real 400 rather than an in-band SSE error event.
    reject_unservable_request(model, req)

    upstream: Optional[OpenAIUpstream] = None
    openai_messages: List[Dict[str, Any]] = []
    openai_extra: Dict[str, Any] = {}
    if model.backend == "openai":
        # Validates (400s) before the on-demand spin-up, all before the
        # response starts — not mid-stream.
        upstream, openai_messages, openai_extra = prepare_openai_messages_call(
            model, req
        )
    elif model.backend not in ("claude", "gemini"):
        raise HTTPException(status_code=500, detail=f"unknown backend {model.backend!r}")

    state = AnthropicStreamState(req.model)

    def event_stream() -> Any:
        first_token_ns: Optional[int] = None
        error_type = ""
        track = None
        try:
            if model.backend == "gemini":
                envelope = _run_gemini_backend(model, req)
                yield from iter_buffered_anthropic_sse(envelope, state)
            else:
                if model.backend == "openai":
                    assert upstream is not None  # resolved above for this backend
                    track = _on_demand.tracking(model, upstream.remote).start()
                    source = _openai_events(upstream, openai_messages, openai_extra, req, state)
                else:
                    source = _claude_events(model, req, state)
                with closing(source):
                    for event in source:
                        if first_token_ns is None and state.text_parts:
                            first_token_ns = _record_first_token(span, start_ns)
                        yield event

            _record_last_token(span, start_ns, first_token_ns, state.output_tokens)
        except GeneratorExit:
            # The response closes this generator when the client goes away;
            # an exhausted one is never closed this way.
            stamp_ctx_failure(ctx, ERROR_CLIENT_DISCONNECT, "client disconnected mid-stream")
            raise
        except HTTPException as exc:
            error_type = f"http_{exc.status_code}"
            logger.error("Anthropic stream error: %s", exc.detail)
            stamp_ctx_failure(
                ctx,
                error_class_of(exc, error_class_for_status(exc.status_code) or ERROR_INTERNAL),
                str(exc.detail),
            )
            yield anthropic_stream_error(Exception(str(exc.detail)))
        except (ClaudeCLIError, GeminiCLIError, UpstreamError) as exc:
            error_type = "upstream_error"
            logger.error("Anthropic stream error: %s", exc)
            stamp_ctx_failure(ctx, error_class_of(exc), str(exc))
            yield anthropic_stream_error(exc)
        finally:
            if track is not None:
                track.finish()
            _finish_anthropic_stream(
                model, req, state,
                ctx=ctx, span=span, client_id=client_id,
                start_ns=start_ns, error_type=error_type,
            )

    return ClosingStreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )


def _run_buffered(model: Model, req: MessagesRequest) -> Dict[str, Any]:
    """Dispatch a non-streaming /v1/messages call to its backend."""
    if model.backend == "claude":
        return _run_claude_backend(model, req)
    if model.backend == "gemini":
        return _run_gemini_backend(model, req)
    if model.backend == "openai":
        return _run_openai_backend(model, req)
    if (reject := reject_non_chat_backend(model, req.model)) is not None:
        raise reject
    raise HTTPException(status_code=500, detail=f"unknown backend {model.backend!r}")


def _record_buffered(
    model: Model,
    req: MessagesRequest,
    payload: Dict[str, Any],
    *,
    ctx: Any,
    span: Any,
    client_id: str,
    start_ns: int,
) -> None:
    """Ring entry, span attributes, payload capture and metrics for one reply."""
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
    # Text blocks only — a tool_use block's arguments aren't the completion.
    completion_preview = "".join(
        block.get("text") or ""
        for block in payload.get("content") or []
        if block.get("type") == "text"
    )
    set_genai_payload(span, _prompt_preview(req), completion_preview)
    record_route_metrics(
        start_ns,
        model=req.model, backend=model.backend, route=_ROUTE,
        client_id=client_id,
        input_tokens=int(u["input_tokens"]), output_tokens=int(u["output_tokens"]),
    )
    logger.info(
        "<- in=%d out=%d (cache_r=%d cache_w=%d) stop=%s backend=%s",
        u["input_tokens"], u["output_tokens"],
        u["cache_read_input_tokens"], u["cache_creation_input_tokens"],
        payload["stop_reason"], model.backend,
    )


@router.post("/v1/messages")
def messages(req: MessagesRequest, request: Request) -> Response:
    model = _resolve(req.model)
    ctx = getattr(request.state, "obs_ctx", None)
    if ctx is not None:
        ctx.backend = model.backend
        ctx.served_model = model.id
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

    start_ns = time.monotonic_ns()
    try:
        if req.stream:
            return _stream_anthropic_response(
                model,
                req,
                ctx=ctx,
                span=span,
                client_id=client_id,
                start_ns=start_ns,
            )
        env = _run_buffered(model, req)
    except HTTPException as exc:
        record_route_metrics(
            start_ns,
            model=req.model, backend=model.backend, route=_ROUTE,
            client_id=client_id,
            error_type=f"http_{exc.status_code}",
        )
        raise

    payload = _envelope_to_anthropic(env, req.model)
    _record_buffered(
        model, req, payload,
        ctx=ctx, span=span, client_id=client_id, start_ns=start_ns,
    )
    return JSONResponse(payload)


@router.post("/v1/messages/count_tokens")
def count_tokens(req: MessagesRequest, request: Request) -> JSONResponse:
    """Anthropic-shape token counting — same request body as /v1/messages.

    Returns ``{"input_tokens": N, ...}``. The count is *exact* for
    llama-server-backed models (their own tokenizer answers) and explicitly
    flagged ``exact: false`` with a ``warning`` for the subscription-CLI
    backends, which expose no tokenizer — see ``src/routes/token_counting.py``.
    """
    model = _resolve(req.model)
    ctx = getattr(request.state, "obs_ctx", None)
    if ctx is not None:
        ctx.backend = model.backend
    if (reject := reject_non_chat_backend(model, req.model)) is not None:
        raise reject
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    payload = _count_tokens(model, req)
    # Deliberately NOT written to ctx.in_tok: no tokens were consumed, and the
    # Hub tab's usage/cost aggregates sum that field over the request ring.
    logger.info(
        "/v1/messages/count_tokens model=%s backend=%s -> input_tokens=%s exact=%s",
        req.model, model.backend, payload.get("input_tokens"), payload.get("exact"),
    )
    return JSONResponse(payload)
