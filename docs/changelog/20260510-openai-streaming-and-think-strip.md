# What we did — 2026-05-10

Two related fixes so OpenAI-shape clients (notably openClaw's vllm
provider) can use the local Qwen / GLM backends through the hub:

1. **SSE streaming pass-through** on `POST /v1/chat/completions`.
   When `stream: true`, the hub now opens a streaming connection to
   the upstream `llama-server` and re-emits each SSE line back to the
   client as a proper `text/event-stream` response (with `data: ...`
   frames and the trailing `data: [DONE]`). Previously the hub
   accepted `stream: true` but always returned a single JSON object —
   openClaw's vllm provider, which always streams, parsed zero deltas
   and reported "No text output returned".

2. **Server-side `<think>` block stripping** for Qwen3-style reasoning
   models. The chat template embedded in `unsloth/Qwen3.5-9B-GGUF`
   doesn't honor `enable_thinking=false` from
   `chat_template_kwargs`, top-level args, or the `/no_think` prefix
   — so thinking is forced. Now:
   - `qwen` and `glm` are launched with `--reasoning-format none`,
     which keeps thinking inline in `content` (between `<think>` and
     `</think>` tags) rather than splitting it into a separate
     `reasoning_content` field that OpenAI-shape clients ignore.
   - The hub strips those `<think>...</think>` blocks before
     responding. Stateful `ThinkStripper` for streaming carries a
     small buffer across SSE chunks so a tag split at a chunk
     boundary is still recognised. Non-streaming path uses
     `strip_think_blocks()` on the final string.

## Why this combination

Three options were on the table:

- **(a) Disable thinking entirely** via the chat template — blocked,
  see above. Requires a different GGUF or a custom chat template.
- **(b) Switch openClaw to a non-thinking model** (gemma4-26b-a4b-it,
  gemma4-e4b-it). Works but doesn't fix anything for users who do
  want qwen3.5-9b's reasoning quality.
- **(c) Let the model think, but hide the thinking from OpenAI-shape
  clients.** Picked.

(c) keeps the model itself unchanged and makes every OpenAI-shape
caller "just work" without touching the chat template, the GGUF, or
the client. The trade-off is that thinking still consumes the token
budget — at low `max_tokens` (e.g. 64), the model can spend the
whole budget thinking and emit empty `content`. So callers using
qwen3.5-9b with the OpenAI shape should plan for `max_tokens >= 1024`
to leave room for both thinking and the answer. Verified that 256
yields empty `content` for "what is 2+2?" while 2048 yields a clean
"2 plus 2 is 4." in 9 non-empty deltas.

## Files changed

- **`src/openai_upstream.py`** —
  - `call_openai_chat_stream()`: streams upstream SSE lines via
    `httpx.Client.stream("POST", …)`. Yields each line for the caller
    to filter.
  - `strip_think_blocks(text)`: regex-based scrub of complete
    strings.
  - `ThinkStripper`: stateful filter for streamed deltas. Two modes
    (`out`, `in`); retains the last few chars at chunk boundaries so
    a `<think` or `</think` straddling chunks is still recognised
    after the next chunk arrives.
  - `clean_openai_response(resp)` /
    `clean_openai_chunk(chunk, strippers)` /
    `iter_cleaned_sse(raw_lines)`: apply the strip + reasoning fold
    in non-streaming and streaming shapes.
  - `openai_response_text()`: now also strips `<think>` blocks (used
    by the Anthropic-shape `/v1/messages` path).
- **`src/server.py`** —
  - Added `_stream_openai_passthrough()`: builds a
    `StreamingResponse(media_type="text/event-stream")` whose
    generator pulls cleaned SSE lines from
    `iter_cleaned_sse(call_openai_chat_stream(...))` and emits them
    with proper line endings. Adds `Cache-Control: no-cache`,
    `Connection: keep-alive`, `X-Accel-Buffering: no` headers so
    intermediaries (and `httpx`'s buffer) don't hold chunks.
  - `chat_completions()`: returns the streaming variant when
    `req.stream=True` and `model.backend == "openai"`. For other
    backends with `stream=True` (currently only `claude`), still
    falls back to a single non-streaming response with a logged
    warning — Anthropic-shape SSE event translation is out of scope
    for this change.
  - Non-streaming OpenAI passthrough now wraps the response with
    `clean_openai_response()` so curl / OpenAI-SDK callers also get
    clean `content`.
  - Updated the module docstring's caveat block to reflect the new
    partial streaming support.
- **`config/models.yaml`** — added `--reasoning-format none` to
  `qwen.args` and `glm.args`. Comment explains why (server-side
  strip works on inline `<think>` tags but not on the separate
  `reasoning_content` field).
- **`tests/test_streaming.py`** (new, 13 tests) — covers
  `ThinkStripper` over chunked input (open and close tags split
  across chunks, no-tag passthrough, unterminated mid-stream),
  `clean_openai_response` (strip + fold + don't-clobber-content),
  `iter_cleaned_sse` end-to-end, and the FastAPI endpoint with
  `TestClient.stream(...)` (proxies SSE, header is
  `text/event-stream`, upstream errors are forwarded inside an SSE
  frame).
- **`README.md`** — updated the "Limitations" section: streaming is
  now supported on `/v1/chat/completions` (OpenAI shape only).

## Validation

- `pytest -q` → 33 passed (was 20 + 13 new).
- Live: stopped hub (PID 47492) and qwen llama-server (PID 46760),
  restarted via `python -m src.run_backend hub` /
  `python -m src.run_backend qwen`.
- `POST http://127.0.0.1:8000/v1/chat/completions` non-stream,
  qwen3.5-9b, max_tokens=256, "What is 2+2?": 200 OK,
  `content` = `"\n\n2+2 equals 4."`, no `<think>` tags. 223
  completion tokens (most went to thinking; the strip removed them).
- Same call with `stream: true`, max_tokens=2048: 200 OK,
  `Content-Type: text/event-stream`, 242 chunks, 9 non-empty,
  reassembled content = `"\n\n2 plus 2 is 4."`, no `<think>` in any
  delta, `data: [DONE]` received.
- Direct `POST :8081/v1/chat/completions` to bypass the hub
  confirmed `--reasoning-format none` is in effect at the upstream
  (raw `<think>` tags appear inline in `delta.content`).

## Out of scope

- Anthropic-shape (`/v1/messages`) SSE event translation
  (`message_start` / `content_block_delta` / `message_delta` /
  `message_stop`). Still on the README backlog. Not needed for
  openClaw's vllm provider.
- Tool-use round-trip translation across shapes for qwen/glm —
  unchanged.
- Changing the qwen GGUF or its embedded chat template. We work
  around forced thinking; we don't try to disable it.
- A `--reasoning-format` toggle in the registry to opt models out of
  the strip. If we ever ship a non-thinking-by-default reasoning
  model, we can add this then.
