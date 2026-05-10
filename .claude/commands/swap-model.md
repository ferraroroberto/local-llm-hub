---
description: Interactively swap one (or more) of the active local roles to a different model. Reads the latest frontier run, asks the user what to change, and edits config/models.yaml + writes a launcher + (optionally) downloads the weights. Human-in-the-loop. Never touches the `claude` subscription path.
---

You are running a **manual, ad-hoc role swap** for the local LLM hub.
The plan is built interactively with the user — do not assume what
they want; ask. Plan-mode rules apply throughout.

## Hard constraints

- **Never** touch the `claude` model row in `config/models.yaml` or
  any `claude-*` alias. The Anthropic subscription path is not a
  local role.
- **Never** delete or overwrite `models/*` weights without explicit
  user confirmation in this session. (Adding new weights via
  `scripts/download_models.py` is fine after they confirm.)
- The four valid local roles are: `agentic_light`, `agentic_heavy`,
  `audio.transcribe`, `audio.translate`. Anything else is out of scope.
- Edit `config/models.yaml` as text (or with surgical replacements).
  Do **not** round-trip the whole file through `yaml.safe_dump` — it
  will strip the heavy in-line commenting that documents each entry.

## Workflow

### 1. Load context (read-only)

- `config/models.yaml` — current `models:`, `roles:`, and the active
  host's `enabled:` list. Resolve the active host via
  `python -m src.host_profile` if needed (or just trust `default: true`
  + `LOCAL_LLM_HUB_HOST`).
- `docs/frontier/runs/LATEST` → that run's `report.md` and
  `frontier.json` for the recommendations (`shortlist`).
- `launchers/` — list existing `run_*.bat` / `.sh` files so we can
  template a new one consistently.

### 2. Show the current state to the user

Print a compact summary:

```
Active host: pc-cuda
Roles right now:
  agentic_light    → gemma4_e4b
  agentic_heavy    → gemma4_26b
  audio_transcribe → whisper
  audio_translate  → whisper_translate
Latest frontier recommendations (docs/frontier/runs/<date>/):
  agentic_light    → upgrade   to qwen3_4b   (reason: <one line>)
  agentic_heavy    → keep
  audio_transcribe → runtime_upgrade (faster-whisper) — manual engine work
  audio_translate  → watch     (fallback only)
```

### 3. Ask the user what to do

One question at a time, with multi-choice where applicable:
- Which role(s) to act on this session
- For each role, which action: `keep` | `upgrade` | `runtime_upgrade` |
  `retire` | `watch`
- For `upgrade`: target model_id (default = shortlist's
  `recommended_id`). If the id is not yet in `models.yaml`, ask for
  `hf_repo`, `hf_pattern`, `model_path`, `port`, and any non-default
  `args` (e.g. `-c`, `--flash-attn on`, `--reasoning-format none`).
  Use the previous run's metadata + the report's per-model notes as
  defaults.
- For `runtime_upgrade`: which runtime, and confirm the user
  understands this is a documented change only (engine code is manual).
- For `retire`: confirm twice. Refuse if it would leave the role
  empty unless paired with an `upgrade` in the same session.
- Whether to download weights now (`scripts/download_models.py
  --only <id>`).
- Whether to also remove the previous role-holder from the host's
  `enabled:` list (default: leave it in for ad-hoc bring-up).

### 4. Show the planned diff and confirm

Before any write, print a clear diff:

```
Will edit config/models.yaml:
  + models.qwen3_4b: { display_name: qwen3-4b-2507, backend: openai, ... }
  ~ roles.agentic_light.model_id: gemma4_e4b → qwen3_4b
  ~ hosts.pc-cuda.enabled: + qwen3_4b
Will write launchers/run_qwen3_4b.bat, .sh
Will run: python scripts/download_models.py --only qwen3_4b   (~3 GB)
Will append to docs/frontier/runs/<latest>/report.md §10
```

Get explicit user approval ("yes" / "go") before executing.

### 5. Execute

- Edit `config/models.yaml` in place (text edits, preserve comments).
- Write `launchers/run_<id>.bat` and `run_<id>.sh` modeled after a
  sibling launcher in the same family. Set the title and the python
  invocation to the new id; everything else is boilerplate.
- If the user approved download: shell out to
  `& .\.venv\Scripts\python.exe scripts/download_models.py --only <id>`
  on Windows (or `./.venv/bin/python ...` on POSIX) and stream the
  output. If it fails, stop and report — do not proceed with the
  remaining steps.
- Append a new row to the **§10 Current decisions** table in the
  latest run's `report.md`, including today's date in the "Decided"
  column. Update the existing role row if one exists.

### 6. Verify and report

- Re-read `config/models.yaml` and the role's row to confirm the
  pointer is correct.
- Confirm the new launcher pair exists and contains the right
  `src.run_backend <id>` invocation.
- Tell the user the **next manual steps**:
  - Stop the old backend if it's running:
    `python -m src.run_backend stop <old_id>` (or stop from the
    Streamlit Models tab)
  - Start the new backend:
    `launchers/run_<new_id>.bat` (or from the Models tab)
  - Run the smoke suite to verify end-to-end:
    `& .\.venv\Scripts\python.exe scripts/smoke_test.py`
- Optionally write a short changelog note under
  `docs/changelog/<today>-swap-<role>.md`.

### 7. Stop

Do **not** restart the hub or the tray. Do **not** edit the
`claude` row. Do **not** edit `tray/config.py` autostart unless the
user explicitly asked.

## What success looks like

- `roles.<role>.model_id` flipped to the new id
- `models.<new_id>` row present with all the fields needed by
  `src.run_backend`
- Active host's `enabled:` list contains the new id
- `launchers/run_<new_id>.bat` and `.sh` exist and reference the new id
- (If requested) weights downloaded to `models/`
- `docs/frontier/runs/<latest>/report.md` §10 reflects the new
  decision with today's date
- Streamlit Frontier tab's "Current role decisions" panel shows the
  new id on next refresh
- Smoke test passes for the new backend
