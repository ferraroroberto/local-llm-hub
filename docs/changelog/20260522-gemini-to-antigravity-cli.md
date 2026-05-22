# 2026-05-22 — Migrate the Gemini backend to the Antigravity CLI (`agy`)

## What was done

Google is deprecating the standalone `gemini` CLI — it stops serving
Google AI Pro / Ultra subscribers on **2026-06-18** — in favour of the
**Antigravity CLI** (`agy`). The hub's `gemini-*` route previously
shelled out to `gemini -p`. This change repoints it at `agy`, keeping
the three Gemini rows (`gemini_pro` / `gemini_flash` / `gemini_lite`)
and their request/response contract identical, so downstream callers
see no difference.

Two `agy` quirks drove the design (see issue #5 for the analysis):

1. **`agy -p` print mode is a TUI.** It renders the reply to a console
   device and writes nothing to a redirected stdout pipe — captured
   empty under plain `subprocess.run`. The hub now spawns `agy` under a
   Windows **ConPTY** (via `pywinpty`) and strips the ANSI control
   sequences from the rendered output, which in print mode is just the
   answer plus a few terminal-init escapes.
2. **`agy` has no per-call model flag.** The model is global persisted
   state, changed through the interactive `/model` picker — the switch
   *does* persist to later `agy -p` processes. `call_gemini()` switches
   the globally-selected model with a short interactive ConPTY session
   whenever the requested model differs from the one last selected,
   then runs print mode. All Gemini calls are serialized behind a lock
   so concurrent requests cannot interleave the global switch.

Auth is unchanged in spirit — `agy` uses a silent keyring login against
the Google account and its AI Pro / Ultra quota, no API key.

### Model mapping

The `display_name` of each row is now the exact `agy` `/model` picker
label (the routing key as well as the UI name):

| Row alias | `display_name` (`agy` picker label) |
|---|---|
| `gemini_pro` | `Gemini 3.1 Pro (High)` |
| `gemini_flash` | `Gemini 3.5 Flash (High)` |
| `gemini_lite` | `Gemini 3.5 Flash (Medium)` |

## Files modified

- `src/gemini_cli.py` — rewritten: ConPTY-driven `agy` wrapper
  (`_Pty` reader thread, `_switch_model`, `_print_call`, `_parse_picker`,
  ANSI stripping). Same `call_gemini()` signature and envelope.
- `config/models.yaml` — three Gemini `display_name` values repointed to
  `agy` picker labels; section comment updated.
- `requirements.txt` — added `pywinpty>=2.0; platform_system == "Windows"`.
- `src/install.py` — `_check_gemini_cli()` now probes `agy`.
- `app/views/install.py` — check description updated.
- `tests/test_gemini_cli.py` — rewritten for the `agy` wrapper.
- `tests/test_router.py`, `tests/test_image_blocks.py` — Gemini
  `display_name` strings updated.
- `README.md`, `docs/model-comparison.md` — usage and reference docs.

## Validation

- `python -m py_compile src/gemini_cli.py src/install.py src/server.py` — OK.
- `python -m pytest -q` — 53 passed.
- Real `agy` calls through `call_gemini()` for all three rows — each
  returned the correct model's reply (~16 s incl. model switch).
- Hub restarted; `POST /v1/messages` and `POST /v1/chat/completions`
  exercised for `gemini_pro` / `gemini_flash` / `gemini_lite` — all
  returned correct Anthropic-/OpenAI-shaped responses.

## Known limitations

- Gemini calls are serialized (global model state); switching model
  between calls adds a one-time interactive ConPTY step (~10–20 s).
- The Gemini path is Windows-only (ConPTY / `pywinpty`). The Mac host
  enables no `gemini-*` rows, so this is not a regression.
- Token counts are still reported as zero — `agy` does not surface them.
- The hub trusts its in-process record of the last-selected model; if
  the model is changed in the Antigravity IDE between hub calls, the
  next call may use the wrong model until the hub switches again.
