---
description: Refresh the local-AI efficient-frontier research, regenerate report.md + frontier.html + frontier.json under docs/frontier/runs/<today>/, and repoint LATEST. Read-only on config/models.yaml — to act on the new run, use /swap-model.
---

You are running the **monthly local-AI frontier refresh** for this
repo. Producing artifacts only — never modify `config/models.yaml`,
`launchers/`, `models/`, or `tray/`. Acting on the result is a
separate step driven by `/swap-model`.

## Inputs

1. Read `docs/frontier/RESEARCH_PROMPT.md` (the canonical research brief).
2. Read `docs/frontier/runs/LATEST` and the previous run's `report.md`
   for diff context (what was on the frontier last time).
3. Read `config/machine_specs.yaml` if present (hardware reference).
4. Read `config/models.yaml` → `models:` and `roles:` for the current
   incumbents (used to compute the recommended action per role).

## Workflow

1. **Plan-mode rules apply.** If anything in the research brief is
   ambiguous for this run, surface the question to the user before
   writing any artifacts.
2. **Pick today's date.** Use the actual current date in `YYYY-MM-DD`
   form. Confirm with the user if the bootstrap run already used
   today's date — overwrite vs. bump.
3. **Run the research.** Use web search to gather:
   - SOTA open-weights models released or majorly updated in the last
     90 days (Qwen, Gemma, Mistral, GLM, Phi, Llama, DeepSeek non-coder,
     GPT-OSS, Granite, Hermes, Ministral, plus newcomers)
   - Current BFCL / IFEval / FLORES-200 / Arena standings (no coding
     benchmarks per the brief)
   - HF Open ASR Leaderboard for whisper-class + Granite Speech +
     Qwen3-ASR + NeMo Canary / Parakeet
   - Recent r/LocalLLaMA threads, HF trending-30-day, llama.cpp
     release notes for perf changes
   - Published RTX 5060 Ti benchmarks (or triangulate from 4070 /
     4060 Ti 16 GB if 5060 Ti numbers are scarce — flag estimates)
4. **Apply the brief's honesty rules.** Date-stamp every claim,
   estimate flags where benchmarks are missing, ≤3 % composite gap
   = call it a tie, surface license restrictions.
5. **Compute recommendations per role.** For each of `agentic_light`,
   `agentic_heavy`, `audio_transcribe`, `audio_translate`, decide
   one of: `keep` | `upgrade` | `runtime_upgrade` | `retire` | `watch`.
   Use the brief's quality weights (35/25/25/15) and the workload mix.
6. **Write three artifacts** under `docs/frontier/runs/<today>/`:
   - `report.md` — didactic markdown following the §1–§9 structure of
     the previous run, plus a **§10 Current decisions** table that
     mirrors `config/models.yaml` → `roles:` as it stands right now
     (i.e., the *incumbent* mapping; recommendations go into §5
     shortlist, not §10).
   - `frontier.json` — machine-readable data: `run_date`, `hardware`,
     `weights`, `tier_info`, `models[]`, `asr_models[]`,
     `concurrency_recipes[]`, `top_picks[]`, `shortlist{}` keyed by
     role with fields `current`, `current_id`, `recommended`,
     `recommended_id`, `recommended_action`, `recommended_runtime`
     (optional), `reason`. The shortlist is what `/swap-model` reads.
   - `frontier.html` — standalone interactive chart (React 18 +
     Recharts via CDN, single `<script type="text/babel">` block,
     data inlined via `<script id="frontier-data" type="application/json">`).
     Match the design of the previous run's `frontier.html`.
7. **Repoint `docs/frontier/runs/LATEST`** to contain just `<today>\n`.
8. **Stop.** Print a one-paragraph summary of the four role
   recommendations and the diff vs. last run. Do **not** edit
   `config/models.yaml`, write launchers, download weights, or touch
   `models/`. Tell the user: "When ready to act on these
   recommendations, run `/swap-model`."

## What success looks like

- `docs/frontier/runs/<today>/{report.md,frontier.json,frontier.html}`
  all present and self-consistent
- `docs/frontier/runs/LATEST` resolves to today
- The Streamlit Frontier tab picks up the new run on next refresh
  (cached for 60s) and shows the new report + chart with the unchanged
  `roles:` decisions panel
- Zero changes to `config/models.yaml`, `launchers/`, `models/`,
  `tray/`, `src/`, `tests/`
