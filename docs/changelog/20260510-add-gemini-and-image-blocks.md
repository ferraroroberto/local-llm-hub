# 2026-05-10 — Add Gemini subscription route + image content blocks

## What changed

Added a second subscription-backed cloud path next to the existing
`claude-*` family: `gemini-*` routes shell out to Google's official
`gemini -p` CLI, using whatever auth the CLI has cached locally
(typically a browser sign-in via `gemini /auth login`). The CLI honors
Google AI Pro / AI Ultra quotas on the signed-in account, so a personal
subscription unlocks Gemini 3.1 Pro from any client pointed at the hub
— no API key, no GCP project, no Vertex AI.

Three concrete model rows are registered, matching the current Gemini
lineup as of 2026-05-10:

| Hub id              | display_name              | Role alias              | Notes |
|---------------------|---------------------------|-------------------------|-------|
| `gemini_pro`        | `gemini-3.1-pro`          | `frontier_reasoning`    | Preview; AI Pro/Ultra required since 2026-03-25. 1 M-token context. |
| `gemini_flash`      | `gemini-3-flash`          | —                       | Released 2026-04-22. Code Assist free-tier quota. |
| `gemini_flash_lite` | `gemini-3.1-flash-lite`   | `frontier_fast`         | GA 2026-05-07. Lowest-latency tier. |

The role aliases mirror the `agentic_light` / `agentic_heavy` pattern
on the local rotation — clients can address `frontier_reasoning` and
survive a future Gemini 3.2 promotion by moving the alias rather than
breaking every caller.

As a second feature in the same patch: **image content blocks** now
work on both the `claude-*` and `gemini-*` paths. Anthropic-shape image
blocks (`{"type":"image", "source":{"type":"base64", ...}}`) are
base64-decoded into a per-request temp dir, then handed to the CLI:

- Claude path: `claude --add-dir <tmp>` + absolute paths prepended to
  the prompt so the agent reads them.
- Gemini path: `@<absolute-path>` tokens prepended to the prompt (the
  CLI's standard file-injection syntax).

The temp dir is auto-cleaned via a context manager when the response
returns. URL-typed image sources fall back to a text reference (no
download today). Local `llama-server` backends are still text-only and
now return a clear 400 instead of silently dropping image content.

## Files modified

**New**
- `src/gemini_cli.py` — subprocess wrapper, parallels `claude_cli.py`.
  Folds the system prompt into stdin (the CLI has no `--system-prompt`
  flag) and accepts `images=` for `@path` injection. `CREATE_NO_WINDOW`
  on Windows so tray-launched hubs don't open terminal windows per call.
- `tests/test_gemini_cli.py` — six tests, fully mocked subprocess.
- `tests/test_image_blocks.py` — eight tests covering the temp-dir
  lifecycle, base64 decoding, URL fallback, multi-image ordering, and
  the local-backend 400.
- `docs/changelog/20260510-add-gemini-and-image-blocks.md` — this file.

**Changed**
- `config/models.yaml` — three new `gemini_*` rows under `models:` with
  the role aliases. No `enabled:` list changes needed; the registry
  always-enables `backend: gemini`, mirroring `backend: claude`.
- `src/model_registry.py` — `enabled_models()` now always-enables
  `backend in {"claude", "gemini"}` instead of just `"claude"`.
- `src/server.py` — adds `GeminiCLIError` / `call_gemini` imports,
  `_run_gemini_backend()`, dispatch for `backend == "gemini"` on both
  `/v1/messages` and `/v1/chat/completions`. Adds `_extract_image_blocks`
  context manager and a 400 for image blocks on `backend == "openai"`.
  `ContentBlock` gains an optional `source` field for image payloads.
- `src/claude_cli.py` — new `images: Sequence[Path]` parameter; passes
  parent dirs via `--add-dir` and prepends absolute-path references to
  the prompt.
- `src/install.py` — new `_check_gemini_cli()` check (warn-only;
  treated the same as `_check_claude_cli` since neither blocks the hub
  from booting).
- `tests/test_server.py` — existing fakes updated to accept the new
  `images=` kwarg.
- `tests/test_router.py` — new tests for Gemini routing on both
  endpoints + role alias resolution; `/v1/models` assertion extended.
- `README.md` — Active rotation, Limitations, Layout, scope/usage
  policy, code examples.
- `docs/model-comparison.md` — three new rows for the Gemini models +
  two new "Roles at a glance" rows for the frontier_* aliases.
- `app/views/welcome.py` — new "Subscription-backed cloud routes"
  section + Gemini code example + updated caveats.
- `app/views/models.py` — new `_render_gemini_card()` next to the
  claude card.
- `app/views/install.py` — help expander mentions the new gemini check.

## Why

The user has a German Google AI Pro subscription and was using `claude
-p` to route the Anthropic subscription through the hub. The same
pattern works for Gemini: official CLI + headless flag + cached
browser-login credentials. As of 2026-03-25 Google requires a paid
subscription for `gemini-3.1-pro` in the CLI, so AI Pro stops being
"just the chat app upgrade" and becomes the actual unlock for the
top-tier Gemini reasoning model from the terminal — exactly the
Claude Code pattern.

Image blocks were already on the backlog (README mentioned it under
"backlog for improvement"). Adding the Gemini route was the right
moment to ship them — both CLIs accept file references via similar
syntaxes (`--add-dir` vs `@path`), and the extraction logic is shared.

## Validation run

```bat
& .\.venv\Scripts\python.exe -m pytest tests/ -q
# 50 passed in 1.89s
```

CLI smoke (requires `gemini` installed and authed):

```bat
:: Start the hub in another terminal: run_hub.bat
curl -s http://127.0.0.1:8000/v1/messages ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"gemini-3.1-pro\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with: pong\"}]}"
```

## Limits / known issues

- Streaming on `/v1/messages` is still single-JSON for both CLI paths —
  same Anthropic-shape streaming gap that already affected `claude-*`.
- Token counts on the Gemini path are zero; the CLI does not surface
  them. If usage accounting matters, use `GEMINI_API_KEY` directly
  (not via this hub) or wait for the CLI to emit usage data.
- Gemini CLI quota is shared with Gemini Code Assist. Heavy hub use
  can starve the IDE assistant on the same account.
- `gemini-3.1-pro` is a preview model and may be renamed or pulled by
  Google; the `frontier_reasoning` role alias insulates clients from
  that. Move the alias in `config/models.yaml` if the underlying name
  changes.
