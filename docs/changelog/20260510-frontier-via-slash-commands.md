# What we did — 2026-05-10 (Frontier pipeline → slash commands)

A worked example of over-engineering, catching it, and pivoting to a
smaller design that fits the actual operator (one human, monthly,
in the loop). Read this if you're tempted to build a programmatic
swap pipeline for an ad-hoc personal tool.

## The original goal

The handoff in `docs/tasks/2026-05-10-frontier-pipeline.md` asked for
a recurring monthly **local-AI efficient-frontier refresh**:

- Research: regenerate a report + interactive chart + JSON for the
  current set of open-weights LLMs and ASR models on this hardware
- Decide: pick what to do with each *active local role*
  (`agentic_light`, `agentic_heavy`, `audio_transcribe`,
  `audio_translate`)
- Rewire: edit `config/models.yaml`, write launchers, download
  weights, sync the tray, write a changelog
- Verify: smoke-test every active role + the dependent apps
  (`transcribe_voice`, openClaw)

The handoff was thorough — four phases, ten data contracts, a
Streamlit decision UI, a test plan, even a sketch of the swap
module's action enum (`keep | upgrade | runtime_upgrade | retire |
watch`).

## What we built first (the over-engineering)

### Phase 1 — Bootstrap artifacts on disk

Wrote the canonical research brief at
`docs/frontier/RESEARCH_PROMPT.md`. Materialised the May 2026 run
under `docs/frontier/runs/2026-05-10/`:

- `report.md` — the didactic research report
- `frontier.json` — the data behind the chart, machine-readable
- `frontier.html` — standalone interactive chart (React 18 +
  Recharts via CDN, single `<script type="text/babel">` block,
  data inlined)
- `LATEST` — flat pointer file containing `2026-05-10\n`

Verifiable, harmless, useful. ✅

### Phase 2 + 3 — The decision UI and the swap engine (the mistake)

Built `src/model_swap.py` (UI-free, ~300 lines) with:

- A `SwapResult` / `RoleResult` dataclass tree
- Five action implementations (`keep` / `upgrade` /
  `runtime_upgrade` / `retire` / `watch`)
- Decision-schema validation that hard-refused any reference to the
  `claude` subscription row
- Idempotent text-edit helpers for `config/models.yaml`
  (`_enable_model_on_host`, `_set_role_model_id`,
  `_ensure_roles_section`, `_write_launcher_pair`)
- A CLI entry point with `--apply` flag, BOM-tolerant JSON loader,
  Windows console encoding workarounds
- 16 pytest cases covering all five actions, claude-row protection,
  dry-run idempotency, download-runner injection

Built `app/views/frontier.py` (~350 lines) with a full per-role
decision UI:

- Action `selectbox` with help tooltips and an inline reference
  expander for all five action semantics
- `model_id` dropdown populated from `config/models.yaml`
- "✨ Apply recommendation" button with `st.dialog` confirmation
  showing the diff before pre-filling the form
- Save / Dry-run / Apply buttons with two-click confirmation on Apply
- Decision preview panel rendering the JSON about to be saved

Built two new top-level concepts: a `roles:` section in
`config/models.yaml` mapping role → model_id, and a candidate row
`qwen3_4b` (with a guessed `hf_repo`) so the "upgrade" path had
something to point at.

49/49 tests passed. Streamlit booted. The "Apply recommendation"
button worked. The user clicked **Apply (rewire hub)**. The result:

```
Apply (live):
  - agentic_heavy      keep             → skipped  no-op (keep)
  - agentic_light      upgrade          → skipped  'qwen3_4b' already
                                                   in models.yaml
  - audio_transcribe   runtime_upgrade  → noted    runtime annotation
                                                   = 'faster-whisper';
                                                   engine code change
                                                   is manual
  - audio_translate    watch            → skipped  no-op (watch)
```

Three problems became visible in that one screenshot:

1. The **upgrade was skipped, not executed**. My logic refused to
   re-add `qwen3_4b` to `models.yaml` (correct), but didn't take the
   next step — flip the role pointer, enable on the host, write a
   launcher, download weights. The user reasonably expected the
   button labelled "Apply" to *apply*.
2. The **runtime upgrade was a no-op masquerading as success**. There
   is no `faster-whisper` engine in `src/run_backend.py`, so the
   action couldn't do anything except write a note. That note had no
   effect on the running hub.
3. The whole thing was **a lot of code for a workflow that runs once
   a month**, and the user is the only operator. Adding the missing
   steps for upgrade (text-edit the host's `enabled:` list, surgical
   yaml mutation that survives PyYAML's comment-eating round-trip,
   launcher templating, weights download, latest-only invariant
   enforcement) was several hundred more lines, more tests, and more
   edge cases.

When the user said "this is a mess", they were right.

## The pivot

Two observations made the pivot obvious:

- **The operator is in the loop.** This isn't an automated CI
  pipeline. A human reads the report, decides what to swap, and
  watches the change happen. There's no value in encoding "what to
  do" as JSON and validating it.
- **The hard part is the messy ad-hoc editing.** Splitting an
  `enabled: [a, b, c]` line, inserting a new model row in the right
  spot of a heavily-commented YAML file, generating a launcher that
  matches the family of an existing one — that's exactly what
  Claude Code is good at. It already understands the codebase. It can
  ask clarifying questions when the answer matters. It can show a
  diff before writing.

So we replaced the engine + UI with **two slash commands**:

- `/frontier-refresh` — runs the research, regenerates the three
  artifacts under `docs/frontier/runs/<today>/`, repoints `LATEST`.
  **Read-only on `config/models.yaml`.** No destructive work.
- `/swap-model` — interactive role swap. Reads the latest run +
  current roles, asks the user one question at a time (which role,
  which target, hf_repo if not registered, download now?), shows the
  planned diff, asks for "yes / go", then edits `config/models.yaml`
  + writes the launcher pair + optionally shells out to
  `scripts/download_models.py`. Updates the §10 "Current decisions"
  table in the run's `report.md` so the source of truth and the
  view stay in sync.

The Streamlit Frontier tab dropped from a full decision UI to a
**read-only viewer**: run picker → 4 metric cards from `roles:`
(read straight from `models.yaml`) → report markdown → embedded chart
HTML. No buttons.

## Files changed

### Deleted

- `src/model_swap.py` (300 lines)
- `tests/test_model_swap.py` (16 tests)
- `app/views/fit.py` — the Fit estimator page; `src/fit_estimator.py`
  + `src/machine_specs.py` are kept as utilities for
  `scripts/detect_machine_specs.py`
- `docs/changelog/2026-05-10-frontier-swap.md` — generated by the
  live Apply test we no longer need
- `docs/frontier/runs/2026-05-10/decision.json` — generated by the
  decision UI we removed

### Added

- **`.claude/commands/frontier-refresh.md`** — the research slash
  command. Tells Claude Code to read `RESEARCH_PROMPT.md`, do the
  web research, write the three artifacts under
  `docs/frontier/runs/<today>/`, repoint `LATEST`, **never** modify
  `config/models.yaml` / `launchers/` / `models/`, and stop with a
  summary of the four role recommendations.
- **`.claude/commands/swap-model.md`** — the interactive swap. Reads
  current state + latest recommendations, asks one question at a
  time, shows the planned diff, confirms, then edits text files
  surgically (preserving comments). Hard-refuses anything touching
  the `claude` row. Updates the report's §10 decisions table after
  each swap.

### Edited

- **`app/views/frontier.py`** — rewritten as read-only. Run picker +
  Current-decisions panel (4 metric cards from `roles:`) + report
  markdown + chart HTML. ~120 lines, no `model_swap` import, no
  buttons.
- **`app/app.py`** — Fit page entry removed.
- **`config/models.yaml`** —
  - Speculative `qwen3_4b` candidate row removed (we never installed
    it; the slash command will add a real row when an actual upgrade
    happens).
  - `qwen` and `glm` removed from `hosts.pc-cuda.enabled` (demoted
    from active rotation per the May 2026 frontier reading). Their
    `models:` rows stay so `launchers/run_qwen.bat` /
    `run_glm.bat` still work for ad-hoc bring-up.
  - `tray.autostart_models` synced to the new active rotation:
    `[gemma4_e4b, whisper, whisper_translate]` (was `[qwen, …]`,
    which would have logged "model not enabled" on tray start).
  - Added the `roles:` section as declarative documentation:

    ```yaml
    roles:
      agentic_light:
        model_id: gemma4_e4b
      agentic_heavy:
        model_id: gemma4_26b
      audio:
        transcribe:
          model_id: whisper
        translate:
          model_id: whisper_translate
    ```

    The Streamlit Frontier tab reads this; `/swap-model` rewrites it.
- **`docs/model-comparison.md`** —
  - Demoted models removed from the technical specs table; a callout
    now lists them under "Demoted candidates (kept defined)".
  - The "Roles at a glance" table is restructured from informal
    role names ("Default agentic / coding") to the formal four
    (`agentic_light`, `agentic_heavy`, `audio_transcribe`,
    `audio_translate`) so it lines up with `roles:` in the yaml.
  - Added a row for `whisper-medium-translate` (the lazy CPU sibling
    on `:8091`) which had been missing.
- **`docs/frontier/runs/2026-05-10/report.md`** — appended a new
  **§10 Current decisions** section. This is the surface
  `/swap-model` updates per swap; it stays in sync with
  `config/models.yaml` → `roles:`.
- **`tests/test_router.py`** + **`tests/test_streaming.py`** — pinned
  test model switched from `qwen3.5-9b` (now demoted) to
  `gemma4-e4b-it`. Port assertion updated `8081 → 8086`.

### Kept (worth listing — these are the durable pieces of Phase 1)

- `docs/frontier/RESEARCH_PROMPT.md` — canonical research brief, read
  by `/frontier-refresh` every month
- `docs/frontier/runs/2026-05-10/{report.md,frontier.json,frontier.html}`
- `docs/frontier/runs/LATEST`

## Why this combination

Two design choices to call out:

### Why slash commands instead of a Python orchestrator

A `frontier_pipeline.py` orchestrator was on the original plan. It
would have wrapped the same research + render + verify steps behind
a `run_research()` function plus a `verify_active_roles()` HTTP
prober. Skipped because:

- The research step requires a live LLM doing web search and
  judgement — that's *exactly* what a slash command body is, and
  Claude Code's native search + file tools beat anything we'd code
  on top.
- The verify step — POSTing a 1-second silent WAV to whisper, hitting
  the small + big LLMs without "thinking" — is twelve lines of
  `httpx`. If we want it later, it lives in `scripts/smoke_test.py`,
  not a new module.
- A Python orchestrator that *calls* Claude Code is an indirection
  over what the user types into the CLI anyway.

### Why two commands instead of one

The handoff envisioned a single `/frontier-refresh` that did both
the research and the rewiring. We split them because:

- Research is **safe and re-runnable** — read-only on the registry.
  You can run it any time, throw away the result, run it again.
- Swapping is **destructive and review-worthy** — edits the registry,
  potentially downloads gigabytes, requires watching the diff. Should
  not happen as a side-effect of "I just want a fresh report."

Two commands, two intents, two confidence levels.

### Why keep the `roles:` section in `config/models.yaml`

It's harmless declarative state — just four `model_id:` strings —
and gives the slash command + the Streamlit tab a single source of
truth for "who fills which role right now". Without it, both would
have to *infer* the role mapping from comments or filenames, which
is brittle and goes stale.

It is **not** code-driving. `src.model_registry` does not consult
it; the hub doesn't read it; the installer ignores it. It exists for
documentation and for `/swap-model`'s edits.

## Validation

- `pytest -q` → 33 passed (back to baseline; we deleted the 16
  `test_model_swap.py` tests and adjusted two pinned-model
  assertions).
- `streamlit run app/app.py --server.headless true` boots clean,
  no stderr.
- `config/models.yaml` invariants verified by ad-hoc script:
  - `claude` row backend == `"claude"` (the user's hard requirement)
  - `qwen3_4b` candidate row gone
  - `qwen`, `glm` not in `hosts.pc-cuda.enabled`
  - `roles:` section present with the four role keys
- Streamlit Frontier tab loads, run picker defaults to `2026-05-10`,
  Current-decisions panel shows the four current model_ids, report
  + chart render.
- Both slash commands appear in Claude Code's available-skills list:
  `frontier-refresh` and `swap-model`.

## What this episode taught

A few lessons worth remembering, in increasing order of importance:

1. **PyYAML round-tripping eats comments.** If you load a
   heavily-commented YAML, mutate a dict, and dump it back, you lose
   every comment. For files where the comments *are* the
   documentation (`config/models.yaml` is one), either use
   `ruamel.yaml` (extra dep) or do surgical text edits with regex
   anchors. Or, in this case, sidestep the problem by having a
   slash command do the edit interactively.
2. **Inline vs. block style is a hidden gotcha.** `enabled: [a, b, c]`
   and `enabled:\n  - a\n  - b\n  - c` are equivalent YAML but
   different *bytes*. A regex tuned for one breaks on the other,
   and `yaml.safe_dump` will silently switch styles. Two of the
   three failing tests in the upgrade-path fix attempt were caused
   by exactly this — the test fixture's `safe_dump` flipped the
   style, the regex stopped matching.
3. **"Latest-only" is a UX claim, not a code invariant.** The
   handoff demanded that exactly one model per role exist after any
   apply. Implementing that as code means: refuse retire if it
   leaves a role empty, auto-remove the previous role-holder on
   upgrade, etc. Implementing that as a slash command means:
   "Claude, when you swap, ask the user if they want to remove the
   previous one." Same outcome, an order of magnitude less code,
   and the user's judgement is in the loop where it should be.
4. **A "successful" Apply that does nothing useful is worse than a
   refused one.** The first live Apply returned `ok` because all
   four actions completed *technically*: skipped twice, watched
   once, noted once. Zero changes to the running hub. The user
   reasonably expected progress. Lesson: when an action's "happy
   path" is a no-op, that's a smell — either the action shouldn't
   exist, or it should refuse with a clear "nothing to do here"
   message instead of looking like success.
5. **Match the tooling to the operator.** A monthly, human-in-the-loop
   ad-hoc workflow doesn't need: schemas, validators, dry-run modes,
   confirmation dialogs, idempotency tests, download runners,
   structured `RoleResult` return types, hard-refusal rules with
   error catalogues. It needs: a smart agent that reads the current
   state, asks good questions, shows a diff, and edits files when
   you say yes. Slash commands are the right shape.
6. **The handoff's senior-dev check almost caught this.** §11 of
   the handoff said "Don't generate the chart HTML by string-
   concatenating React. Use a real Jinja2 template." That was good
   advice for the chart. The same instinct — *don't roll your own
   when a better tool exists* — applied to the entire swap engine.
   We just didn't notice in time.

## Out of scope

The following pieces of the original handoff are **deferred**, not
forgotten. If the slash commands prove too thin, these come back —
but only on demand:

- `src/frontier_pipeline.py` orchestrator. Research is now done by
  `/frontier-refresh` directly.
- `scripts/smoke_test.py` extension with the "standard suite of
  without-thinking calls" (silent WAV to whisper :8090 + plain
  prompts to gemma4_e4b + gemma4_26b). Easy to add when needed —
  it's ~30 lines of `httpx`.
- A `faster-whisper` engine in `src/run_backend.py` +
  `src/backend_process.py`. The May 2026 frontier reading
  recommends it; until someone writes the engine code, the
  recommendation lives in `report.md` §10 with the note "Engine
  code change pending".
- Tray autostart syncing on swap. `tray.autostart_models` is now
  synced manually to match the active roles; `/swap-model` could
  also update it, but for one human + four roles + monthly cadence,
  manual is fine.
- Programmatic verification of dependent apps (`transcribe_voice`,
  openClaw). The user explicitly said this should be a "standard
  suite" they'd customize later — same place as the smoke-test
  extension above.

## Files at a glance after the dust settled

```
.claude/commands/
├── frontier-refresh.md   NEW — produces a run, never edits the registry
├── swap-model.md         NEW — interactive swap, edits the registry
└── system-specs.md       (unchanged)

app/
├── app.py                EDIT — Fit page entry removed
└── views/
    ├── frontier.py       REWRITE — read-only viewer (run picker +
    │                                roles panel + report + chart)
    ├── comparison.py     (unchanged — renders the markdown)
    ├── models.py         (unchanged — already filters via enabled_models())
    ├── server.py         (unchanged)
    ├── playground.py     (unchanged)
    ├── testing.py        (unchanged)
    ├── install.py        (unchanged)
    └── welcome.py        (unchanged)

config/
└── models.yaml           EDIT — qwen/glm demoted from active rotation,
                                  roles: section added, autostart synced

docs/
├── frontier/
│   ├── RESEARCH_PROMPT.md
│   └── runs/
│       ├── LATEST                  → 2026-05-10
│       └── 2026-05-10/
│           ├── report.md           EDIT — §10 Current decisions added
│           ├── frontier.json
│           └── frontier.html
├── model-comparison.md   EDIT — active rotation only, demoted callout,
│                                whisper-medium-translate row added
└── changelog/
    └── 20260510-frontier-via-slash-commands.md   THIS FILE
```
