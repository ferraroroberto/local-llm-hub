# Frontier workflow — bi-weekly model refresh and role swaps

How the hub's active local models get re-evaluated and swapped: a
recurring workflow built from a research skill plus a **human-in-the-loop**
swap command, and a small tree of on-disk run artifacts. Research runs
unattended on a schedule; acting on it is deliberately *not* automated — a
human reads the research, decides what to swap, and watches the change
happen.

## The two entry points

Research and rewiring are split with two different confidence levels.

### `/frontier-refresh` — research (safe, re-runnable, scheduled)

A skill at `.claude/skills/frontier-refresh/SKILL.md` — the **single
owner** of the research brief (workloads, quality weights, honesty
rules), the cadence, and the deterministic output contract. It does the
web research and writes a dated run under `docs/frontier/runs/<today>/`:

- `report.md` — the didactic research report. Fixed sections and
  fixed-column tables so consecutive runs diff mechanically: §0 is the
  per-role **verdict table** (`role | incumbent | verdict | best
  alternative | gap | reason`), §8 is the cumulative **progression
  table** (run date × role — it only ever grows), §10 is the live
  *Current decisions* table `/swap-model` edits;
- `frontier.json` — the data behind the chart, machine-readable (the
  `shortlist{}` block is what `/swap-model` consumes — its schema is
  frozen);
- `frontier.html` — a standalone interactive chart (data inlined);

then repoints the `docs/frontier/runs/LATEST` pointer file and posts the
verdict table as a comment on the always-open **frontier ledger issue
[#272](https://github.com/ferraroroberto/local-llm-hub/issues/272)**
(`audit-meta`-labelled, same pattern as the codebase-audit ledger #32) —
the ledger's last comment always answers "what runs in each role, what's
the best alternative, am I missing something?".

It is **read-only on `config/models.yaml`, `launchers/`, and `models/`**
— no destructive work — and stops with a summary of the verdicts for the
active local roles (`agentic_light`, `agentic_heavy`, `audio_transcribe`,
`audio_translate`). Because it is read-only you can run it any time,
throw the result away, and run it again.

**Scheduling:** the skill's `run-weekly.bat` is registered as a weekly
job (FRI 02:30) in app-launcher's Jobs tab and self-skips alternate weeks
(fixed-epoch week parity), giving an unattended **bi-weekly** cadence.
Headless constraint (fleet-config#314): every step runs synchronously —
a `claude -p` session that backgrounds a step and ends its turn dies
silently.

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
└── runs/
    ├── LATEST                flat file containing the latest run date
    └── <YYYY-MM-DD>/         one dir per run
        ├── report.md         didactic report (§0 verdict, §8 progression, §10 current decisions)
        ├── frontier.json     machine-readable run data
        └── frontier.html     standalone interactive chart
```

The brief itself lives in the skill
(`.claude/skills/frontier-refresh/SKILL.md`) — there is no separate
research-prompt document, so cadence, weights, and structure have exactly
one owner.

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

- **A skill and a slash command, not a Python orchestrator.** The
  research step needs a live model doing web search and judgement — that
  *is* what a skill body is, and Claude Code's native search + file tools
  beat anything coded on top. The verify step (POSTing a silent WAV to
  whisper, pinging the small + big LLMs) is a few lines of `httpx` that
  belong in `scripts/smoke_test.py`, not a new module. A Python
  orchestrator that *calls* Claude Code is just indirection over what the
  operator types anyway.
- **Two entry points, not one.** Research is safe, re-runnable, and can
  run unattended; swapping is destructive and review-worthy. Two intents,
  two confidence levels.
- **Deterministic tables, didactic prose.** The report's fixed sections
  and fixed-column tables make run-over-run diffs mechanical (and let the
  ledger comment be generated verbatim from §0); the LLM narrative is
  kept, scoped to a short paragraph per section explaining *why* the
  tables say what they say.
- **Match the tooling to the operator.** Swapping stays human-in-the-loop:
  a smart agent that reads the current state, asks good questions, shows
  a diff, and edits files when you say yes — no schemas, validators, or
  dry-run modes needed.

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
