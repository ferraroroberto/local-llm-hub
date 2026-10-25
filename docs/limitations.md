# Limitations (intentional — lightweight)

- **Streaming.** `POST /v1/chat/completions` proxies llama-server SSE through,
  and `POST /v1/messages` emits the Anthropic event sequence expected by
  `client.messages.stream(...)`. Claude text arrives incrementally from the
  CLI's `stream-json` output; llama-server deltas are translated while
  scrubbing `<think>...</think>` blocks. The current `agy` adapter has no
  incremental source, so Gemini requests still receive valid Anthropic SSE
  but their answer arrives as one buffered text delta.
- Multi-turn chats are flattened into a single prompt for `claude -p`.
  (The local backends handle multi-turn natively through llama-server.)
- **`claude -p` runs isolated from your own Claude Code setup**.
  Each call passes `--strict-mcp-config --setting-sources ""
  --disable-slash-commands` and sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
  on the child only, so a hub caller never sees your `CLAUDE.md` files,
  auto-memory, hooks, MCP servers, plugins or skills. Built-in tools are
  off (`--tools ""`) except `Read` when the request carries image or
  document blocks, which the model reads from the per-request `--add-dir`.
  Your own interactive sessions and `~/.claude/settings.json` are
  untouched. `--bare` is not used because it refuses subscription (OAuth)
  auth. Tradeoff: with no settings sources, the `settings.json` OTel
  exporter env doesn't reach the child, so hub-driven calls reach the Code
  Usage tab through their session transcripts only (session persistence
  stays on), not live OTel deltas.
- **Tool use works on both shapes for the local backends**.
  OpenAI-shape callers get native tool calls from llama-server's
  `--jinja` templates; Anthropic-shape callers send `tools` /
  `tool_choice` on `POST /v1/messages` and receive `tool_use` content
  blocks with `stop_reason: "tool_use"`, buffered or streamed
  (`content_block_start` → `input_json_delta` → `content_block_stop`).
  Feed the block back as a `tool_result` to continue the round trip. Two
  translation notes: an Anthropic user turn carrying N `tool_result`
  blocks becomes N OpenAI `tool` messages, and Anthropic's server-side
  tools (no `input_schema`) are refused with a 400 since they have no
  local equivalent. Streamed tool arguments are accumulated and replayed
  as one complete `input_json_delta` — Anthropic keeps one content block
  open at a time while OpenAI may interleave calls, and llama-server
  parses tool calls out of the finished generation anyway. **The CLI
  backends refuse tool use with a 400:** `claude-*` / `gemini-*` dispatch
  flattens a conversation to one text prompt, so `tools` and `tool_use` /
  `tool_result` blocks have nowhere to go — answering in prose instead
  would be a well-formed wrong answer.
  - **Caveat — `tool_choice: {"type": "any"}` fails on the thinking-capable
    local models.** It translates to OpenAI's `tool_choice: "required"`,
    which llama-server rejects when the chat template opens a `<think>`
    block (`Failed to initialize samplers: Unexpected empty grammar stack
    after accepting piece: <think>`). This is a llama-server
    grammar/template limitation, not a hub bug — a direct POST to the
    backend's own `:8088/v1/chat/completions` returns the identical error —
    so the hub surfaces the upstream message verbatim rather than silently
    downgrading the choice. Use `auto` (or a no-think model id such as
    `qwen3.5-4b-nothink`) if you need forced tool calling.
- **Stop and sampling parameters on `/v1/messages`**. The
  local backends receive `stop_sequences` (sent upstream as `stop`),
  `top_p` and `top_k`, buffered or streamed. llama-server doesn't report
  which stop word matched, so a hit still ends with `stop_reason:
  "end_turn"`. The CLI backends have no stop control, so `stop_sequences`
  on a `claude-*` / `gemini-*` model is a 400 rather than a reply that
  runs past the marker; `temperature`, `top_p` and `top_k` are accepted
  there and ignored. **Extended thinking is refused everywhere:**
  `thinking: {"type": "enabled"}` returns a 400 because the hub emits no
  thinking blocks; `{"type": "disabled"}` is accepted.
- **Image and document content blocks are supported on the `claude-*`
  and `gemini-*` subscription paths** — the hub base64-decodes each
  `image` / `document` block to a per-request temp dir, adds that dir to
  the CLI workspace (`claude --add-dir` / `agy --add-dir`) and references
  the files inline (`@<basename>` for `agy`). Adding the dir to the
  workspace is what makes `agy` resolve the reference deterministically
  instead of searching the filesystem (which read attachments only
  intermittently). `document`
  blocks accept any file the CLI can read: PDF, plus text/data/code
  files (JSON, CSV, Markdown, …); the `media_type` picks the temp-file
  extension and unknown types fall back to `.bin` (still read as bytes).
  Local `llama-server` backends (`qwen3.5-*`, `gemma4-*`) are text-only
  and return 400 with a hint to retry on a subscription model. URL
  sources degrade to a text reference (not fetched). Extended-thinking
  blocks are still dropped at the shape boundary. **OpenAI-shape inline
  documents are supported too:** on `POST /v1/chat/completions`, send a
  `file` content part with `filename` and either raw-base64 `file_data` or
  a `data:<media-type>;base64,...` URL. It reaches the same request-scoped
  attachment path. `file_id` (no local `/v1/files` store), `file_url`
  (no authenticated fetch contract), and `image_url` remain explicit 400s;
  use Anthropic-shape `/v1/messages` for image input. Other non-`text` /
  non-`file` OpenAI content parts are also refused rather than silently
  dropped.
- Token counts reflect what each backend reports in its response. The
  `agy` CLI does not surface token counts, so usage on the `gemini-*`
  path is reported as zero.
- **Gemini calls are serialized.** `agy` selects its model from global
  persisted state, so the hub holds a lock across the model switch and
  the print call. Concurrent `gemini-*` requests run one at a time;
  switching model between calls adds a one-time interactive step.
- **The `agy` attachment path can only be verified locally.** GitHub CI (`windows-latest`) has no authenticated `agy` / Gemini subscription. `tests/chat/test_gemini_attachments_live.py` is the live regression guard for the `--add-dir` attachment handling — it is skipped by default and must be run manually on the Windows reference box after any change to `src/gemini_cli.py`'s attachment handling: `$env:HUB_LIVE_GEMINI = "1"; .venv/Scripts/python.exe -m pytest tests/chat/test_gemini_attachments_live.py -v`.

