# Frontier workflow — monthly model refresh and role swaps

How the hub's active local models get re-evaluated and swapped: a
monthly, **human-in-the-loop** workflow built from two Claude Code slash
commands and a small tree of on-disk run artifacts. It is deliberately
*not* an automated pipeline — a human reads the research, decides what to
swap, and watches the change happen.

## The two slash commands

Research and rewiring are split into two commands with two different
confidence levels.

### `/frontier-refresh` — research (safe, re-runnable)

Reads the canonical brief at
[docs/frontier/RESEARCH_PROMPT.md](frontier/RESEARCH_PROMPT.md), does the
web research, and writes a dated run under
`docs/frontier/runs/<today>/`:

- `report.md` — the didactic research report;
- `frontier.json` — the data behind the chart, machine-readable;
- `frontier.html` — a standalone interactive chart (data inlined);

then repoints the `docs/frontier/runs/LATEST` pointer file. It is
**read-only on `config/models.yaml`, `launchers/`, and `models/`** — no
destructive work — and stops with a summary of the recommendations for
the active local roles (`agentic_light`, `agentic_heavy`,
`audio_transcribe`, `audio_translate`). Because it is read-only you can
run it any time, throw the result away, and run it again.

### `/swap-model` — rewiring (destructive, review-worthy)

Reads the latest run plus the current roles, then asks **one question at
a time** (which role, which target model, `hf_repo` if the model isn't
registered, download now?), shows the planned diff, and only on a "yes /
go" edits `config/models.yaml` **surgically** (preserving comments),
writes the launcher pair, and optionally shells out to
`scripts/download_models.py`. It **hard-refuses** anything touching the
`claude` subscription row, and updates the run report's §10 *Current
decisions* table so the registry and the report stay in sync.

Swapping is kept separate from research precisely because it edits the
registry, may download gigabytes, and needs the diff watched — it should
never happen as a side-effect of "I just want a fresh report."

## Run artifacts

```
docs/frontier/
├── RESEARCH_PROMPT.md        canonical brief; read by /frontier-refresh
└── runs/
    ├── LATEST                flat file containing the latest run date
    └── <YYYY-MM-DD>/         one dir per run
        ├── report.md         didactic report (§10 = current decisions)
        ├── frontier.json     machine-readable run data
        └── frontier.html     standalone interactive chart
```

`report.md` and `frontier.html` are opened directly — there is no admin
UI viewer for them.

## The `roles:` section in `config/models.yaml`

Roles are declared as harmless declarative state — four `model_id:`
strings — in `config/models.yaml`:

```yaml
roles:
  agentic_light:
    model_id: qwen3_4b
  agentic_heavy:
    model_id: gemma4_26b
  audio:
    transcribe:
      model_id: whisper
    translate:
      model_id: whisper_translate
```

It is the single source of truth for "who fills which role right now".
`/swap-model` rewrites it. It is **not** code-driving: `src.model_registry`
does not consult it, the hub does not read it, the installer ignores it.
Without it, the mapping would have to be *inferred* from comments or
filenames, which goes stale.

## Why this shape

- **Slash commands, not a Python orchestrator.** The research step needs
  a live model doing web search and judgement — that *is* what a slash
  command body is, and Claude Code's native search + file tools beat
  anything coded on top. The verify step (POSTing a silent WAV to
  whisper, pinging the small + big LLMs) is a few lines of `httpx` that
  belong in `scripts/smoke_test.py`, not a new module. A Python
  orchestrator that *calls* Claude Code is just indirection over what the
  operator types anyway.
- **Two commands, not one.** Research is safe and re-runnable; swapping is
  destructive and review-worthy. Two intents, two confidence levels.
- **Match the tooling to the operator.** A monthly, human-in-the-loop
  workflow does not need schemas, validators, dry-run modes, confirmation
  dialogs, idempotency tests, or structured result types. It needs a
  smart agent that reads the current state, asks good questions, shows a
  diff, and edits files when you say yes.

## Lessons for editing `config/models.yaml`

These bite anything that mutates the heavily-commented registry
(`/swap-model` in particular):

- **PyYAML round-tripping eats comments.** Load a commented YAML, mutate a
  dict, dump it back, and every comment is gone. For a file where the
  comments *are* the documentation, do surgical text edits with regex
  anchors (or use `ruamel.yaml`) — or, as here, have a slash command do
  the edit interactively.
- **Inline vs. block style is a hidden gotcha.** `enabled: [a, b, c]` and
  the block form are equivalent YAML but different *bytes*. A regex tuned
  for one breaks on the other, and `yaml.safe_dump` silently switches
  styles.
- **"Latest-only" is a UX claim, not a code invariant.** Enforcing "exactly
  one model per role" as code means refusing retires that empty a role,
  auto-removing the previous holder on upgrade, and so on. As a slash
  command it is one instruction — "when you swap, ask whether to remove
  the previous one" — with the operator's judgement in the loop.
