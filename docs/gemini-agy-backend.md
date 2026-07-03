# Gemini backend via the Antigravity CLI (`agy`)

Reference for the hub's `gemini-*` rows, which route to Google's
**Antigravity CLI (`agy`)** — the replacement for the deprecated
standalone `gemini` CLI. This is the concrete backend behind the general
method in
[playbook-cli-backend-migration.md](playbook-cli-backend-migration.md).
For per-row specs see [model-comparison.md](model-comparison.md).

## What the contract guarantees

Downstream callers see **no difference** from any other backend. The
public contract is unchanged:

- the same row aliases (`gemini_pro`, `gemini_flash`, `gemini_lite`);
- the same Anthropic-/OpenAI-shaped request and response envelopes;
- the same routing (`backend: gemini` in `config/models.yaml`).

Only the subprocess invoked underneath (now `agy`, previously `gemini`)
and each row's `display_name` are allowed to change. `call_gemini()` in
[src/gemini_cli.py](../src/gemini_cli.py) keeps its signature and envelope
identical, so `src/server.py` needs no changes.

Auth is subscription-based: `agy` uses a silent keyring login against the
Google account and its AI Pro / Ultra quota — no API key.

## The two `agy` quirks that shape the wrapper

`agy` is an agent-first CLI, not a headless one. Two properties drive the
whole design:

1. **`agy -p` print mode is a TUI.** It renders the reply to a console
   device and writes nothing to a redirected stdout pipe — captured empty
   under a plain `subprocess.run`. The hub spawns `agy` under a Windows
   **ConPTY** (via `pywinpty`) and strips the ANSI control sequences from
   the rendered output, which in print mode is just the answer plus a few
   terminal-init escapes.
2. **`agy` has no per-call model flag.** The model is global persisted
   state, changed only through the interactive `/model` picker — and the
   switch *does* persist to later `agy -p` processes. `call_gemini()`
   switches the globally-selected model with a short interactive ConPTY
   session whenever the requested model differs from the one last
   selected, then runs print mode. **All Gemini calls are serialized
   behind a lock** so concurrent requests cannot interleave the global
   switch.

### Model selection

Each row's `display_name` is the **exact `agy` `/model` picker label**,
which doubles as the routing key. The current row-alias → picker-label
mapping lives in [config/models.yaml](../config/models.yaml) (and
[model-comparison.md](model-comparison.md)); it is repointed when Google
changes the picker labels, without touching the stable `aliases`.

## Known limitations (empirical caveats)

- **Serialized calls.** Because model selection is global CLI state,
  Gemini calls run one at a time; switching model between calls adds a
  one-time interactive ConPTY step (~10–20 s).
- **Windows-only.** The path depends on ConPTY / `pywinpty`. The Mac host
  enables no `gemini-*` rows, so this is not a regression there.
- **Token counts are zero.** `agy` does not surface usage, so the Gemini
  path reports usage as zero.
- **Stale last-selected-model.** The hub trusts its in-process record of
  the last-selected model; if the model is changed in the Antigravity IDE
  between hub calls, the next call may use the wrong model until the hub
  switches again.
