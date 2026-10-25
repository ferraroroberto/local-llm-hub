"""Anthropic SSE event adapters: Claude CLI, OpenAI-upstream and buffered
envelopes translated into the ``/v1/messages`` streaming event sequence.

Re-exported by ``src/chat_translation.py``, which is the import path callers use.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from ..claude_cli import ClaudeCLIError

# Logs under the module name these adapters shipped in, so log filters keep matching.
logger = logging.getLogger("src.chat_translation")


@dataclass
class AnthropicStreamState:
    """Mutable result accumulated while translating one Anthropic SSE stream.

    ``next_index`` / ``open_index`` / ``text_index`` track content-block
    allocation. Anthropic keeps exactly one block open at a time and numbers
    them in emission order, so indices are handed out as blocks are opened
    rather than fixed in advance — a text-only stream still gets a single
    block at index 0, unchanged from before tool support (#552).
    """

    requested_model: str
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:24]}")
    text_parts: List[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str = "end_turn"
    message_started: bool = False
    message_delta_sent: bool = False
    message_stopped: bool = False
    next_index: int = 0
    open_index: Optional[int] = None
    text_index: Optional[int] = None
    # Upstream tool-call fragments keyed by OpenAI ``tool_calls[].index``:
    # ``{"id": str, "name": str, "args": [fragment, ...]}``.
    tool_calls: Dict[int, Dict[str, Any]] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


def _anthropic_sse(event: Dict[str, Any]) -> str:
    """Encode one Anthropic event with both its SSE name and JSON body."""
    return (
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


def _set_anthropic_usage(state: AnthropicStreamState, usage: Dict[str, Any]) -> None:
    state.input_tokens = max(
        state.input_tokens, int(usage.get("input_tokens", 0) or 0)
    )
    state.output_tokens = max(
        state.output_tokens, int(usage.get("output_tokens", 0) or 0)
    )
    state.cache_read_tokens = max(
        state.cache_read_tokens,
        int(usage.get("cache_read_input_tokens", 0) or 0),
    )
    state.cache_write_tokens = max(
        state.cache_write_tokens,
        int(usage.get("cache_creation_input_tokens", 0) or 0),
    )


def _start_anthropic_stream(state: AnthropicStreamState) -> List[str]:
    if state.message_started:
        return []
    state.message_started = True
    event = {
        "type": "message_start",
        "message": {
            "id": state.message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": state.requested_model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": state.input_tokens,
                "output_tokens": 0,
                "cache_creation_input_tokens": state.cache_write_tokens,
                "cache_read_input_tokens": state.cache_read_tokens,
            },
        },
    }
    return [_anthropic_sse(event)]


def _close_open_block(state: AnthropicStreamState) -> List[str]:
    """Close whichever content block is currently open, if any."""
    if state.open_index is None:
        return []
    index = state.open_index
    state.open_index = None
    return [_anthropic_sse({"type": "content_block_stop", "index": index})]


def _start_text_block(state: AnthropicStreamState) -> List[str]:
    events = _start_anthropic_stream(state)
    if state.text_index is not None and state.open_index == state.text_index:
        return events
    # Text arriving after a tool block opens a *new* text block rather than
    # reopening the old one — Anthropic allows several text blocks per
    # message, and a closed block cannot take further deltas.
    events += _close_open_block(state)
    state.text_index = state.next_index
    state.next_index += 1
    state.open_index = state.text_index
    events.append(_anthropic_sse({
        "type": "content_block_start",
        "index": state.text_index,
        "content_block": {"type": "text", "text": ""},
    }))
    return events


def _text_delta(state: AnthropicStreamState, text: str) -> List[str]:
    if not text:
        return []
    events = _start_text_block(state)
    state.text_parts.append(text)
    events.append(_anthropic_sse({
        "type": "content_block_delta",
        "index": state.text_index,
        "delta": {"type": "text_delta", "text": text},
    }))
    return events


def _emit_tool_blocks(state: AnthropicStreamState) -> List[str]:
    """Replay accumulated upstream tool calls as Anthropic ``tool_use`` blocks.

    Buffered to the end of the stream rather than forwarded live. Anthropic's
    wire format keeps exactly one content block open at a time, while OpenAI
    may interleave fragments from several ``tool_calls[].index`` values in any
    order — replaying at the end is the only translation that stays valid for
    both. Nothing incremental is lost in practice: llama-server parses tool
    calls out of the completed generation, so it has no partial arguments to
    stream in the first place.

    The accumulated fragments are emitted as a single ``input_json_delta``,
    which is a complete JSON object by construction — the caller's accumulator
    sees exactly what the buffered route would have returned.
    """
    events: List[str] = []
    emitted = 0
    for openai_index in sorted(state.tool_calls):
        entry = state.tool_calls[openai_index]
        name = entry.get("name") or ""
        partial = "".join(entry.get("args") or []) or "{}"
        if not name:
            logger.warning(
                "dropping upstream tool call at index %s: no function name "
                "(id=%r args=%r)",
                openai_index, entry.get("id"), partial[:200],
            )
            continue
        events += _close_open_block(state)
        index = state.next_index
        state.next_index += 1
        state.open_index = index
        events.append(_anthropic_sse({
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "tool_use",
                "id": entry.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                "name": name,
                "input": {},
            },
        }))
        events.append(_anthropic_sse({
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial},
        }))
        emitted += 1
    if emitted:
        # Authoritative over the upstream's finish_reason, which llama-server
        # does not always set to "tool_calls" when it emits one.
        state.stop_reason = "tool_use"
    elif state.stop_reason == "tool_use":
        # The upstream reported "tool_calls" but every call was unusable and
        # got dropped above. A "tool_use" stop reason with no tool_use block
        # is not a valid Anthropic message — and it would send an agent loop
        # hunting for a call that isn't there.
        logger.warning(
            "upstream finished with tool_calls but emitted no usable tool "
            "call; reporting stop_reason=end_turn instead"
        )
        state.stop_reason = "end_turn"
    return events


def _finish_anthropic_stream(state: AnthropicStreamState) -> List[str]:
    if state.message_stopped:
        return []
    if state.next_index == 0 and not state.tool_calls:
        # Nothing was ever emitted (empty upstream response) — an Anthropic
        # message always carries at least one content block.
        events = _start_text_block(state)
    else:
        events = _start_anthropic_stream(state)
    events += _emit_tool_blocks(state)
    events += _close_open_block(state)
    if not state.message_delta_sent:
        events.append(_anthropic_sse({
            "type": "message_delta",
            "delta": {
                "stop_reason": state.stop_reason,
                "stop_sequence": None,
            },
            "usage": {"output_tokens": state.output_tokens},
        }))
        state.message_delta_sent = True
    events.append(_anthropic_sse({"type": "message_stop"}))
    state.message_stopped = True
    return events


def iter_claude_anthropic_sse(
    records: Iterator[Dict[str, Any]],
    state: AnthropicStreamState,
) -> Iterator[str]:
    """Filter Claude Code JSONL records into a text-only Anthropic stream.

    Claude Code's wrapper emits native Anthropic events nested under
    ``stream_event`` plus its own lifecycle records. Its current models may
    also emit implicit thinking blocks. Those are intentionally filtered here:
    explicit extended-thinking support is a separate API contract, while this
    route has historically exposed only final assistant text.
    """
    for record in records:
        if record.get("type") == "result":
            if record.get("is_error"):
                raise ClaudeCLIError(
                    f"claude -p returned is_error=true: {str(record)[:300]}"
                )
            _set_anthropic_usage(state, record.get("usage") or {})
            if not state.text_parts and record.get("result"):
                yield from _text_delta(state, str(record["result"]))
            continue
        if record.get("type") != "stream_event":
            continue
        event = record.get("event") or {}
        event_type = event.get("type")
        if event_type == "message_start":
            message = event.get("message") or {}
            if message.get("id"):
                state.message_id = str(message["id"])
            _set_anthropic_usage(state, message.get("usage") or {})
            yield from _start_anthropic_stream(state)
        elif event_type == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                yield from _text_delta(state, str(delta.get("text") or ""))
        elif event_type == "message_delta":
            delta = event.get("delta") or {}
            if delta.get("stop_reason"):
                state.stop_reason = str(delta["stop_reason"])
            _set_anthropic_usage(state, event.get("usage") or {})
        elif event_type == "message_stop":
            # Wait for Claude Code's following ``result`` record before
            # closing the downstream stream. It carries the authoritative
            # success/error bit and final usage; emitting message_stop first
            # could otherwise produce an invalid error-after-stop sequence.
            continue
    yield from _finish_anthropic_stream(state)


_OPENAI_STOP_TO_ANTHROPIC = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def iter_openai_anthropic_sse(
    lines: Iterator[str],
    state: AnthropicStreamState,
) -> Iterator[str]:
    """Translate cleaned OpenAI SSE lines into Anthropic Messages events."""
    yield from _start_anthropic_stream(state)
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        usage = chunk.get("usage") or {}
        state.input_tokens = max(
            state.input_tokens, int(usage.get("prompt_tokens", 0) or 0)
        )
        state.output_tokens = max(
            state.output_tokens, int(usage.get("completion_tokens", 0) or 0)
        )
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield from _text_delta(state, content)
            _accumulate_tool_calls(state, delta.get("tool_calls"))
            finish = choice.get("finish_reason")
            if finish:
                state.stop_reason = _OPENAI_STOP_TO_ANTHROPIC.get(
                    str(finish), "end_turn"
                )
    yield from _finish_anthropic_stream(state)


def _accumulate_tool_calls(
    state: AnthropicStreamState,
    tool_calls: Optional[List[Dict[str, Any]]],
) -> None:
    """Fold one chunk's ``delta.tool_calls`` into ``state.tool_calls``.

    OpenAI spreads a single call across chunks: the first carries ``id`` and
    ``function.name``, later ones append ``function.arguments`` fragments that
    are only valid JSON once concatenated. ``index`` is what ties them
    together, so it keys the accumulator rather than list position.
    """
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        index = int(call.get("index", 0) or 0)
        entry = state.tool_calls.setdefault(index, {"id": "", "name": "", "args": []})
        if call.get("id"):
            entry["id"] = str(call["id"])
        function = call.get("function") or {}
        if function.get("name"):
            entry["name"] = str(function["name"])
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            entry["args"].append(arguments)


def iter_buffered_anthropic_sse(
    envelope: Dict[str, Any],
    state: AnthropicStreamState,
) -> Iterator[str]:
    """Shape a buffered backend result as one valid Anthropic SSE sequence."""
    _set_anthropic_usage(state, envelope.get("usage") or {})
    state.stop_reason = str(envelope.get("stop_reason") or "end_turn")
    yield from _start_anthropic_stream(state)
    yield from _text_delta(state, str(envelope.get("result") or ""))
    yield from _finish_anthropic_stream(state)


def anthropic_stream_error(exc: Exception) -> str:
    """Return the Anthropic error event used after an SSE response has begun."""
    return _anthropic_sse({
        "type": "error",
        "error": {"type": "api_error", "message": str(exc)},
    })
