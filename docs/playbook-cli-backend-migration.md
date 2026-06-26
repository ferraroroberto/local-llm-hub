# Playbook — migrating a subscription-CLI backend

Reference method for adapting the hub when a vendor changes the CLI
behind a subscription backend (`claude-*`, `gemini-*`, or a future
one). Vendors rename, deprecate, or restructure their CLIs on their own
schedule; this hub exists precisely to absorb that churn so downstream
callers never feel it. This is the procedure that worked for the
2026-05 `gemini` CLI → Antigravity CLI (`agy`) migration — see
[gemini-to-antigravity-cli.md](gemini-to-antigravity-cli.md)
and the analysis in
[issue #5](https://github.com/ferraroroberto/local-llm-hub/issues/5).

## The invariant that must survive any migration

The hub is a centralizer. After *any* backend change, downstream apps
must see **no difference**:

- the same row aliases (`gemini_pro`, `claude_sonnet`, …) — these are
  the public contract; **never rename them**;
- the same Anthropic-/OpenAI-shaped request and response envelopes;
- the same routing (`backend:` value in `config/models.yaml`).

Only the subprocess invoked underneath, and a row's `display_name`
(which may double as a routing key), are allowed to change.

## Step 1 — Detect and scope the change

- Pin down the **hard date**: when does the old path stop working?
- Identify what *survives*: often an API-key path or an enterprise tier
  keeps working even when the consumer/subscription path is cut.
- Decide the auth model you must preserve (subscription login vs. API
  key) — this is usually the user's call, not yours. Ask.

## Step 2 — Map the current integration

Find every touch point before changing anything. For a CLI backend it
is typically:

- `src/<backend>_cli.py` — the subprocess wrapper (the core).
- `src/server.py` — routing (`backend == "<name>"`) on both API shapes.
- `config/models.yaml` — the rows, `display_name`, `aliases`.
- `src/install.py` + `app/views/install.py` — the on-PATH check.
- `tests/` — wrapper tests, router tests, image-block tests.
- `README.md`, `docs/model-comparison.md` — usage and reference docs.

`grep -ri "<vendor>"` across the repo is the fastest census.

## Step 3 — Verify the new CLI empirically *before* writing code

This is the **decision gate**. Vendor blogs and third-party articles
are unreliable on CLI specifics; the only source of truth is the CLI on
the machine. Install it and confirm, with throwaway spike scripts:

1. **Binary name & PATH shape** — does `shutil.which` resolve it? On
   Windows, is it `.exe` (direct) or `.cmd` (shim, needs care)?
2. **Auth** — does it support the auth model you must keep? Is login
   silent/cached, or interactive?
3. **Headless invocation** — is there a non-interactive prompt mode?
   Does it read the prompt from an argument or stdin?
4. **Machine-readable output** — does it write the reply to stdout, or
   render a TUI? Capture it under a pipe (that is how the hub will run
   it). A TUI may need a pseudo-terminal (ConPTY / pty) to produce
   output at all.
5. **Model selection** — a per-call flag, an env var, or persisted
   global state? This decides whether calls can run concurrently.
6. **System prompt, images, timeouts** — flags or workarounds.

**If a required capability is missing, stop and report.** Do not ship a
wrapper that papers over a missing capability — surface the options
(different transport, different auth, wait for the vendor) and let the
user choose. Half the value of this hub is an honest "this no longer
works the way you think."

## Step 4 — Implement behind the unchanged contract

- Keep the wrapper's function signature and return envelope identical,
  so `server.py` needs no changes.
- Repoint `display_name` if the new CLI's model identifiers changed;
  leave `aliases` untouched.
- If the new CLI is messier (TUI, global model state, no token counts),
  isolate that mess inside the wrapper module — a reader thread, a
  lock, output scraping — and document each workaround with *why*.
- Add any new dependency to `requirements.txt` with the right platform
  marker.

## Step 5 — Verify in layers

1. `python -m py_compile` the changed modules.
2. `python -m pytest -q` — wrapper tests fully mock the subprocess; no
   real CLI or network calls.
3. A real call through the wrapper for **every** row (model switching
   included), confirming each returns its own model's output.
4. Restart the hub and exercise both API shapes (`/v1/messages` and
   `/v1/chat/completions`) end to end.

## Step 6 — Record it

- Update `README.md` and `docs/model-comparison.md` if behavior or
  config surface changed.
- Keep the GitHub issue + the PR that closes it as the durable record
  of the investigation and the decision — do not write a dated
  changelog file. If the migration uncovered a reusable concept,
  add a topic-named doc under `docs/` instead.

## Lessons from past migrations

- **`gemini` → `agy` (2026-05).** The new CLI's print mode was a TUI
  with no stdout output and no per-call model flag. Resolved by driving
  it under a ConPTY, scraping ANSI-stripped output, and switching the
  global model through the interactive picker behind a lock. The
  general lesson: an "agent-first" CLI may not be headless-friendly —
  Step 3.4 and 3.5 are where migrations succeed or fail.
