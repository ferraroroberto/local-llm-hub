"""OpenAI-shape chat route: ``POST /v1/chat/completions``.

llama-server rows pass through (SSE proxied, ``<think>`` scrubbed); the
subscription-CLI rows are translated to the Anthropic turn shape, run, and
wrapped back as an OpenAI completion.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .. import on_demand as _on_demand
from ..chat_translation import (
    _extract_media_blocks,
    _flatten_messages,
    _openai_messages_to_anthropic,
    call_openai_upstream,
    resolve_openai_upstream,
)
from ..claude_cli import ClaudeCLIError, call_claude
from ..gemini_cli import GeminiCLIError, call_gemini
from ..model_registry import Model
from ..observability import set_genai_payload, set_genai_request_attrs, set_genai_response_attrs
from ..openai_upstream import UpstreamError, call_openai_chat_stream, clean_openai_response, iter_cleaned_sse
from ..server_common import (
    client_id_from as _client_id_from,
    current_otel_span as _current_otel_span,
    ensure_backend_ready_or_503 as _ensure_backend_ready,
    record_first_token as _record_first_token,
    record_last_token as _record_last_token,
    record_route_metrics,
    resolve_model_or_400 as _resolve,
    stash_trace_id_on_ctx as _stash_trace_id_on_ctx,
)
from .sse_response import SSE_HEADERS, ClosingStreamingResponse, reject_non_chat_backend

logger = logging.getLogger(__name__)
router = APIRouter()

_ROUTE = "/v1/chat/completions"


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


@dataclass
class _ErrorLabel:
    """The ring/metrics error label a failing request is recorded under."""

    value: str = ""


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


def _build_openai_extra(model: Model, req: ChatCompletionRequest) -> Dict[str, Any]:
    """Build the upstream payload overlay for an OpenAI-shape backend call.

    Seeds from the model's server-side ``inject_extra`` (e.g. the no-think
    alias's ``chat_template_kwargs``), then layers caller-sent fields on top
    so the caller always wins. Shared by the streaming and non-streaming
    ``openai`` backend paths so both build the same overlay the same way.
    """
    extra: Dict[str, Any] = dict(model.inject_extra or {})
    if req.tools is not None:
        extra["tools"] = req.tools
    if req.tool_choice is not None:
        extra["tool_choice"] = req.tool_choice
    if req.response_format is not None:
        extra["response_format"] = req.response_format
    if req.chat_template_kwargs is not None:
        extra["chat_template_kwargs"] = req.chat_template_kwargs
    return extra


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


class _StreamUsage:
    """Usage and first-token bookkeeping over one proxied SSE stream."""

    def __init__(self, span: Any, start_ns: int) -> None:
        self.span = span
        self.start_ns = start_ns
        self.first_token_ns: Optional[int] = None
        self.usage_in = 0
        self.usage_out = 0

    def observe(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
            # Detect first non-empty content delta to record TTFT.
            if self.first_token_ns is None:
                delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                if delta.get("content"):
                    self.first_token_ns = _record_first_token(self.span, self.start_ns)
            # Parse usage on every frame — llama-server emits the
            # usage chunk after content, so it arrives after
            # first_token_ns is already set.
            u = obj.get("usage") or {}
            self.usage_in = max(self.usage_in, int(u.get("prompt_tokens", 0) or 0))
            self.usage_out = max(self.usage_out, int(u.get("completion_tokens", 0) or 0))
        except Exception:  # noqa: BLE001
            pass


def _stream_openai_passthrough(
    model: Model,
    req: ChatCompletionRequest,
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
    upstream = resolve_openai_upstream(model)
    extra = _build_openai_extra(model, req)
    # On-demand idle tracking: a locally-served on_demand model must
    # not be idle-unloaded while a stream is in flight — pair the start here
    # with the finish in the generator's ``finally`` (which also runs on a
    # client disconnect, via GeneratorExit).
    track = _on_demand.tracking(model, upstream.remote).start()

    if start_ns is None:
        start_ns = time.monotonic_ns()
    usage = _StreamUsage(span, start_ns)

    def event_stream() -> Any:
        error_type = ""
        try:
            raw = call_openai_chat_stream(
                upstream.base_url,
                model=upstream.model_name,
                messages=req.messages,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                extra=extra or None,
                headers=upstream.headers,
            )
            for cleaned in iter_cleaned_sse(raw):
                usage.observe(cleaned)
                yield cleaned + "\n"
            # SSE record terminator after the final line. llama-server
            # already sends ``data: [DONE]``; the trailing blank line
            # closes the last event for strict SSE parsers.
            yield "\n"

            # Stream finished cleanly — close out telemetry.
            _record_last_token(span, start_ns, usage.first_token_ns, usage.usage_out)
            set_genai_response_attrs(
                span, input_tokens=usage.usage_in, output_tokens=usage.usage_out,
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
            yield "data: " + json.dumps(err) + "\n\n"
            yield "data: [DONE]\n\n"
        finally:
            track.finish()
            record_route_metrics(
                start_ns,
                model=req.model, backend=model.backend,
                route=_ROUTE, client_id=client_id,
                input_tokens=usage.usage_in, output_tokens=usage.usage_out,
                error_type=error_type,
            )

    return ClosingStreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )


def _complete_via_cli(
    model: Model,
    req: ChatCompletionRequest,
    label: _ErrorLabel,
    *,
    span: Any,
    client_id: str,
    start_ns: int,
) -> JSONResponse:
    """Run a claude/gemini row and wrap its reply as an OpenAI completion."""
    # Normalize OpenAI-shape dict messages to Message objects and
    # flatten with the same helper /v1/messages uses.
    # Inline file parts become document blocks here, then reuse the
    # request-scoped attachment extractor used by /v1/messages.
    try:
        turns, sys_text = _openai_messages_to_anthropic(
            req.messages, model_label=model.id,
        )
        with _extract_media_blocks(turns) as (text_turns, attachments):
            prompt = _flatten_messages(text_turns) if text_turns else ""
            call = call_claude if model.backend == "claude" else call_gemini
            env = call(
                prompt,
                model=model.display_name,
                system=sys_text,
                attachments=attachments or None,
            )
    except HTTPException:
        # Unsupported or malformed media is refused before dispatch.
        label.value = "http_400"
        raise
    except (ClaudeCLIError, GeminiCLIError) as e:
        label.value = "upstream_cli_error"
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
    record_route_metrics(
        start_ns,
        model=req.model, backend=model.backend,
        route=_ROUTE, client_id=client_id,
        input_tokens=in_t, output_tokens=out_t,
    )
    return JSONResponse(_wrap_as_openai(
        text, model_name=req.model, in_toks=in_t, out_toks=out_t,
    ))


def _complete_via_openai(
    model: Model,
    req: ChatCompletionRequest,
    label: _ErrorLabel,
    *,
    span: Any,
    client_id: str,
    start_ns: int,
) -> JSONResponse:
    """Pass a buffered llama-server completion through, think-scrubbed."""
    try:
        upstream = resolve_openai_upstream(model)
    except HTTPException:
        label.value = "config_error"
        raise
    extra = _build_openai_extra(model, req)
    # On-demand idle tracking and the UpstreamError -> 502
    # mapping both live in call_openai_upstream, shared with the
    # /v1/messages buffered path. It raises only that 502, so the
    # catch below labels exactly the upstream failure for the ring.
    try:
        raw = call_openai_upstream(
            model,
            upstream,
            req.messages,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            extra=extra,
        )
    except HTTPException:
        label.value = "upstream_http_error"
        raise
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
    record_route_metrics(
        start_ns,
        model=req.model, backend=model.backend,
        route=_ROUTE, client_id=client_id,
        input_tokens=in_t, output_tokens=out_t,
    )
    # Passthrough of upstream response (already OpenAI-shape), with
    # <think>...</think> stripped from message.content and
    # reasoning_content folded into content when content is empty.
    return JSONResponse(cleaned)


@router.post("/v1/chat/completions")
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

    if model.backend == "openai":
        # On-demand lifecycle: a cold ``startup: on_demand`` local
        # backend is spawned here and the request blocks until it answers
        # (distinct 503 on load failure). No-op for eager/remote/virtual rows.
        _ensure_backend_ready(model)

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

    label = _ErrorLabel()
    telemetry = {"span": span, "client_id": client_id, "start_ns": start_ns}
    try:
        if model.backend in ("claude", "gemini"):
            return _complete_via_cli(model, req, label, **telemetry)

        reject = reject_non_chat_backend(model, req.model)
        if reject is not None:
            label.value = "http_400"
            raise reject

        if model.backend == "openai":
            return _complete_via_openai(model, req, label, **telemetry)

        label.value = "unknown_backend"
        raise HTTPException(status_code=500, detail=f"unknown backend {model.backend!r}")
    finally:
        if label.value:
            record_route_metrics(
                start_ns,
                model=req.model, backend=model.backend,
                route=_ROUTE, client_id=client_id,
                error_type=label.value,
            )
