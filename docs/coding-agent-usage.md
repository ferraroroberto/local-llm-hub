# Coding agent usage

The **Code** tab is a passive, multi-vendor view of host-side coding-agent
activity.  It parses each agent's local session logs server-side — zero
subprocesses, no wrapper around any binary, no impact on the running CLIs:

- **Claude Code** (`vendor="claude"`) — the JSONL transcripts at
  `~/.claude/projects/<encoded-path>/*.jsonl` (`src/usage/code_usage.py`).
- **Codex / OpenAI** (`vendor="codex"`) — the rollout JSONL files at
  `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (`src/usage/codex_usage.py`).  One
  record per `token_count` event, using the per-turn delta `last_token_usage`
  (never the cumulative `total_token_usage`, which would double-count).
- **GitHub Copilot** (`vendor="copilot"`, `src/usage/copilot_usage.py`) — two local
  sources, both carrying **exact** billed AI Credits (not a rate-table
  estimate):
  - The **Copilot CLI**'s `~/.copilot/session-state/<uuid>/events.jsonl`,
    written only for a *clean-shutdown* session — one record per model used
    in that session (session-granular; the CLI doesn't expose a per-turn
    breakdown), priced from the `session.shutdown` event's `totalNanoAiu`
    (`credits = totalNanoAiu / 1e9`).
  - **VS Code Copilot Chat**'s per-session
    `%APPDATA%\Code\User\workspaceStorage\<hash>\chatSessions\<uuid>.jsonl`
    event log (macOS: `~/Library/Application Support/Code/...`), which
    carries an exact `copilotCredits` float per request plus the resolved
    model — parsed via a minimal replay of the file's patch stream (not a
    general JSON-patch engine, just the couple of fields usage needs).
  Both sources only see sessions that reached this specific machine and
  wrote a complete log — sessions that crashed mid-flight, or ran
  elsewhere, are invisible to them. A separate **"Copilot credits
  (official)" card** (only shown on the Copilot vendor tab) fills that gap
  with the *authoritative* GitHub billing API — per-day × per-model spend,
  no session/project attribution, requires a `GITHUB_COPILOT_BILLING_PAT`
  fine-grained PAT (`.env`, "Plan: read-only" permission) or the card shows
  "not configured" rather than erroring (`src/usage/copilot_billing.py`,
  `GET /admin/api/code/copilot/billing`).
- **AGY / Antigravity** (`vendor="agy"`, `src/usage/agentsview_usage.py`) — sourced
  from the optional external
  [AgentsView](https://github.com/kenn-io/agentsview) service, which indexes
  `agy`'s local session storage the hub declined to reverse-engineer itself. Merges AgentsView's `gemini` (hub-routed calls) and
  `antigravity-cli` (interactive) slugs into one AGY vendor; other agents
  AgentsView knows about are deliberately not surfaced (curated map in
  `agentsview_usage.py`). Polled over HTTP, never from raw files; the hub
  runs fully without it. AgentsView appears in the Hub tab's **Services**
  sheet and the **Settings › Service startup** toggles — the hub launches it at
  startup when toggled on (exe from `.venv-agentsview/`, `AGENTSVIEW_EXE`,
  or PATH). Setup and behaviour:
  [code-usage-agentsview.md](code-usage-agentsview.md).

**Freshness.** The summary is cached per period × vendor and is never
served more than 25 s old. While a tab keeps polling, the hub rebuilds it in the
background, so the 30 s poll answers from cache instead of re-walking every
transcript (a warm build is several seconds on a busy machine).

**Prewarm.** At hub start the opening view (Day, All vendors) is built in
the background, off the startup path. A restarted hub parses every transcript
once (about 50 s on the tower); doing that at start means the first tab open
finds a fresh summary, and every later build runs off warm parser caches. The
log carries `prewarmed summary today/all in …s`, or a `prewarm … failed`
warning (the first request then builds for itself).

**Layout.** The tab opens on totals and cost: the **Day / Week / Month /
All** switch over the four counters (requests, input, output, cache reads), each
with its ≈ $ cost and a delta badge (green ↑ / red ↓) vs. the previous
comparable period, or "new" when a metric had no activity in the prior window,
and the grand-total ≈ $ cost above them. The header line names a failed load or
an offline AgentsView, else the period and its total cost. On a wide window
(1100px+, mouse) the totals and trend sit left of the breakdowns.

The **agent filter** button beside the period switch opens a sheet: **All**
(every vendor summed), **Claude**, **Codex**, **Copilot**, every AgentsView
extra (e.g. AGY) with its freshness, each with its own requests and ≈ $ for the
period, plus **Sub-agents (OTel)**, a separate source (below). Picking a single
vendor scopes the whole tab to it. The full per-agent table shows on a wide
window in All.

**Sub-agents (OTel)** shows Claude Code's own OTel export for the chosen period
(Date, Project, Model, Source, In, Out, Cache reads, Cache writes, ≈ $), its
tokens-and-cost line and an empty state naming the export to enable. It is the
only source that sees sub-agent usage, is stored separately and is never added
into All.

**By project** lists the busiest projects as rows; **Every project**, **By
model** and **Recent sessions** open as sheets. Projects under the `automation`
workspace are shown by folder name (the `automation-` prefix is stripped; the
workspace root itself stays `automation`), and long names are cut with “…”.
The token definition and the undercount note live behind the ⓘ; a one-line
note stays visible while Claude figures are in view.

The input, output, and cache-read tiles show an **≈ $… equivalent API cost**
— what those tokens would have billed on the metered API, priced
per model from `config/claude_pricing.json` (Anthropic) and
`config/openai_pricing.json` (OpenAI); refresh those files when a provider
changes prices.  Codex usage is subscription-metered, so the ≈ $ figure is an
*estimate* against OpenAI list prices.  **Copilot and AgentsView-sourced
vendors are the exceptions:** Copilot's cost figure is the session/VS-Code
log's own exact `credits_usd`, and AgentsView vendors carry the cost
*as reported by AgentsView* (its own estimate for subscription agents) —
neither is re-priced against the hub's rate tables, even when the underlying
model resolves to a Claude model.

Two cross-vendor token semantics to keep in mind: for **Codex**, `cached_input`
tokens are a *subset* of input (the cost path prices the non-cached remainder at
the input rate and the cached portion at the cheaper cached rate), and
`reasoning_output_tokens` are a *subset* of output — surfaced as an "incl. …
reasoning" tooltip on the output tile's figure (no extra line on the card, so
the four cards stay one height), never added on top.  Claude's
`cache_read` tokens, by contrast, are reported separately/additively.  The
OpenAI >272K long-context surcharge (2× input / 1.5× output) is not modelled.

A **Trend** card
draws the metric of the counter last tapped (input tokens, output tokens,
requests or cache reads) as stacked bars, one per time bucket, with each model
a segment.  Segments take their vendor's hue (Anthropic orange, OpenAI green,
Google blue, Microsoft purple, anything else grey) and, within a vendor, step
down in opacity by usage over the shown range: the most-used model is solid,
then 75 %, 50 %, and a 25 % floor.  The stack and the legend run vendor by
vendor, most-used at the base; the tooltip lists only the models with a value
in that bucket, then the bucket total.  Day → the last 14 days, Week → 7 days,
Month → 30 days (all daily bars); All → weekly bars from the first record,
monthly once that would pass 31 bars.  The **Recent sessions** sheet shows the
last 15 sessions across every project on this host.

> **⚠️ The local JSONL source undercounts Claude two ways.** (a) Sessions bridged through **claude.ai/code** (web/desktop remote-control) write `mode`/`permission` lines but **no assistant/usage records** to the local transcripts — their tokens are only visible via the Code tab's **Sub-agents (OTel)** source: the hub runs its own OTLP-metrics receiver (`POST /v1/metrics`) that Claude Code's telemetry export can point at; see [telemetry-langfuse.md](telemetry-langfuse.md#claude-code-otel-metrics-receiver) for the host env vars. (b) Claude Code **prunes transcripts after ~30 days** (`cleanupPeriodDays`) — to survive that, the hub snapshots daily rollups per (date, vendor, model, project) into `data/code_usage_history.json` (`src/usage/code_usage_history.py`: max-merge on write, per-vendor cutoff on read so no day is double-counted) and feeds them back into the "All" period after the source files are gone. History accumulates from the first snapshot forward; days already pruned before it are unrecoverable from disk. Newer Claude Code also writes per-session directories with **sub-agent transcripts** (`projects/<proj>/<session>/subagents/agent-*.jsonl`), which the parser includes, so sub-agent usage is captured where those exist; finer per-subagent attribution is tracked in the [Claude Code issue tracker](https://github.com/anthropics/claude-code/issues/22625).

> **Why no host OTEL → Langfuse bridge?**  The JSONL counters above
> already give the community-standard tokens-in / tokens-out view, and the
> trace-graph view isn't worth the wiring cost: Langfuse's OTLP receiver only
> ingests traces (not metrics/logs), Claude Code traces are still a beta
> signal, and the per-signal exporter config is fiddly. Revisit if Anthropic
> stabilises tracing or Langfuse adds metrics ingestion.

See [telemetry-langfuse.md](telemetry-langfuse.md) for the
full architecture, what's captured, the X-Trace-Id contract, and
limitations. For client-side trace correlation + feedback posting see
[clients-telemetry-contract.md](clients-telemetry-contract.md).

