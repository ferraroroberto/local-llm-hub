# Machine diagnostics

On-demand deep diagnostics for the machine the hub runs on: press start, and
the hub records what the box is *actually* doing — system-level CPU/RAM/swap/
disk/net/GPU plus a full per-process inventory and the listening-port map —
into a SQLite store, then interprets it (attribution, health verdict, drift).

The question it exists to answer: **"I have a lot of apps running — how much is
each one costing me, is this machine overloaded, and what changed since it was
healthy?"**

Reach it from the admin SPA's **Machines** tab → the *this machine* card's
**🔬 Diagnostics** row.

## Design constraints

| Constraint | How it's met |
| --- | --- |
| **No new resident process** | The sampler is an asyncio task inside the already-running hub. When no capture is active, no task, thread, or timer exists — the feature costs nothing at rest. |
| **OS-agnostic** | Pure `psutil` + stdlib `sqlite3`, plus the existing `nvidia-smi` probe (absent GPU → empty list). The identical capture runs on Windows, macOS, and Linux. |
| **Minable afterwards** | Everything lands in `data/diagnostics.db`. Every run carries a `machine_id`, so DBs copied off different machines merge cleanly. |
| **Replicable per machine** | Each machine runs its own full hub install, so each captures itself. No agent to deploy. |

## Layout

| Module | Role |
| --- | --- |
| `src/diagnostics/sampler.py` | The capture loop; run lifecycle; the opt-in scheduled snapshot |
| `src/diagnostics/store.py` | SQLite schema, migrations, rollup queries, retention |
| `src/diagnostics/attribution.py` | Process → fleet-app mapping; listening-port scan |
| `src/diagnostics/rules.py` | Health-verdict engine |
| `src/diagnostics/report.py` | Summary digest, baseline drift, markdown report |
| `src/diagnostics/settings.py` | Retention + scheduled-snapshot settings |
| `app_web/routers/diagnostics.py` | The `/admin/api/diagnostics/*` API |
| `app_web/static/diagnostics.js` | The drill-in dialog |

## Capture modes

- **One-shot snapshot** — a single immediate sample, stored as a complete run.
  Primes psutil's CPU caches and settles for a second first, so CPU figures are
  real rather than the `0.0` a cold call returns.
- **Timed capture** — samples every `interval_s` (default 15 s, floor 5 s) until
  `duration_s` elapses (default 1 h, ceiling 24 h) or you stop it. Each tick
  subtracts its own work from the sleep so the cadence stays honest on a busy
  box, and a failed tick is logged and skipped rather than ending the run.

Only one capture runs at a time — concurrent captures would double the observer
effect and interleave in the store.

## Attribution — why `app-launcher: 3 procs` beats `python.exe ×14`

`config/diagnostics_apps.json` (committed) maps a process to the app that owns
it, in precedence order:

1. **Fleet root** — a path under a configured automation root
   (`E:/automation/<repo>/…`) attributes to `<repo>`. This catches every sister
   project's `.venv` interpreter, which is how most of the fleet's Python
   processes launch. A sibling worktree (`<repo>-wt-315`) folds into its repo.
2. **Known binary** — `llama-server`, `dockerd`, browsers, OS services…
3. **Cmdline substring** — a few narrow fallbacks.
4. **Path prefix** — the broad net, anchored at the start of the executable
   path: an OS-owned directory (`/System/Library/`, `/usr/libexec/`,
   `C:/Windows/`) identifies a process whose *name* means nothing on its own.
5. **`unattributed`** — everything else.

That last bucket is not a failure mode: it is **the review list of processes
nobody has accounted for yet**, which is exactly what you want when hunting
bloat. Teaching the sampler a new app is a data edit, never a code change.

Only **OS-owned** roots are bucketed by path. `/Applications` and
`/opt/homebrew` deliberately are **not** — user-installed software is precisely
the bloat a capture exists to surface, so auto-filing it would hide the answer.

### Per-OS rule groups

Any group may carry a `_windows` / `_darwin` / `_linux` twin, merged in only on
that platform (`binaries_darwin`, `path_prefixes_linux`, …). This is not
cosmetic: `/usr/bin` is Apple-owned and SIP-protected on macOS but is where
ordinary user software lives on Linux, so bucketing it as "system" is correct
on one and actively wrong on the other. The same lever fixed a live
mis-attribution — Elgato ships a Windows app called *Control Center*, and the
name-only rule was claiming Apple's macOS shell component for it on every Mac
capture.

### macOS reads the executable path, not the command line

Reading another user's command line on macOS needs privileges the hub does not
have, so every `root`/`_service` daemon reports an **empty** cmdline — 310 of
673 processes on the Mac Mini, leaving only a 16-character truncated kernel
name (`AppleCredentialM`). The executable path uses a different kernel call
(`proc_pidpath`) that stays readable, and resolved 308 of those 310. The scan
therefore falls back to `exe` when `cmdline` is empty; without it the path
rules would have nothing to match and 42% of the machine would stay
unattributed no matter how good the rule table was.

Measured effect of the per-OS tables (#320):

| Machine | Before | After |
|---|---|---|
| `mac-mini-m4` (Darwin 25.2) | 565 / 570 groups unattributed (99%) | 3 / 664 (0.5%) |
| `pc-cuda` (Windows 11) | 86 / 542 unattributed | 54 / 542, **0 regressions** |

Windows can only improve here: path rules run last, so they convert
`unattributed` rows and can never re-label one that already had a name. Linux
coverage is written to the same shape but is **unverified against a live
capture** — `openclaw` runs no hub yet (#316).

Grouping is by *cmdline*, not PID — the venv `pythonw` redirector spawns a stub
*and* a real process per launch, so a PID-keyed rollup double-counts one app.
`peak_procs` is the largest **per-tick** distinct-PID count, so a process that
restarts mid-run doesn't inflate its app's apparent concurrency.

## Health verdicts

`config/diagnostics_rules.json` (committed) holds every threshold. A finished
run gets a persisted `healthy` / `warning` / `critical` verdict plus findings,
each carrying the evidence behind it.

| Rule | Fires on |
| --- | --- |
| `cpu.sustained` | CPU above the threshold for a *fraction of the run* — a single spike during a model load is not a finding |
| `ram.pressure` / `swap.pressure` / `disk.capacity` | Peak percentage past warn/critical |
| `gpu.vram` | Per-GPU VRAM saturation |
| `processes.total` | Total process count at peak |
| `processes.per_app` | One app's concurrent process count |
| `processes.unattributed` | Heavyweight processes nobody has accounted for |
| `processes.zombies` | Zombie/defunct processes |
| `ports.duplicate` | One port claimed by more than one app during the run — a restart loop, or two launchers fighting |

**Aggregate buckets are excluded from `processes.per_app`** via
`processes.per_app_ignore` (`unattributed`, `windows-services`, …). Those are
collections of unrelated processes, not one app; judging them as one made a
perfectly healthy box report `critical` on every run.

Rules are pure functions over stored rows, so they are unit-tested against
synthetic fixtures and can re-judge an old capture after a retune:
`POST /admin/api/diagnostics/runs/{id}/evaluate` re-reads the config file — no
hub restart needed.

## Baselines & drift

Mark a representative run as **baseline** (one per machine). Every later run
then reports what changed: peak RAM/CPU, per-app process counts, new or gone
listening ports, and apps that appeared since. This turns creeping bloat from a
vague feeling into a reviewable diff. Retention never prunes a baseline.

## Retention & size

Raw `samples` / `process_samples` / `ports` rows are pruned after
`retention_days` (default 90); run metadata, verdicts, and baselines are kept
indefinitely — they are tiny and they are what long-horizon comparison reads.
Pruning runs opportunistically **at capture start**, so there is no timer to
keep alive.

## Scheduled snapshots (opt-in, default off)

Enabling the daily snapshot in the modal's Settings section arms one more
asyncio task that sleeps between one-shots. That is what makes multi-week trend
lines exist without anyone remembering to press a button — and it still adds no
process. A snapshot is skipped while a manual capture is already running.

The hub also closes runs orphaned by a previous process at startup (marking them
`interrupted`), so a hub that died mid-capture never leaves a row that looks
like a live capture.

## Analysing a run

The UI is a **trigger plus a digest** — deep analysis happens outside it:

- **Health report** (`…/report`) — self-contained markdown designed to be pasted
  into an LLM session and reasoned about cold.
- **Export JSON** (`…/export`) — every stored row for one run. JSON rather than
  the raw `.db` on purpose: a single run exports as a self-describing document,
  while shipping the database would hand over every *other* run too.
- **The SQLite file itself** — `data/diagnostics.db`, for arbitrary queries:

  ```sql
  -- which app grew the most between two runs?
  SELECT app_id, MAX(rss) FROM (
    SELECT app_id, ts, SUM(rss_bytes) AS rss
    FROM process_samples WHERE run_id = ? GROUP BY app_id, ts
  ) GROUP BY app_id ORDER BY 2 DESC;
  ```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/admin/api/diagnostics/status` | Live capture progress + settings |
| `POST` | `/admin/api/diagnostics/start` | Begin a timed capture |
| `POST` | `/admin/api/diagnostics/snapshot` | One-shot sample |
| `POST` | `/admin/api/diagnostics/stop` | Stop the active capture |
| `GET` | `/admin/api/diagnostics/runs` | Past runs, newest first |
| `GET` | `/admin/api/diagnostics/runs/{id}` | Summary digest |
| `GET` | `/admin/api/diagnostics/runs/{id}/drift` | Delta vs the baseline |
| `GET` | `/admin/api/diagnostics/runs/{id}/report` | Markdown health report |
| `GET` | `/admin/api/diagnostics/runs/{id}/export` | Raw rows as JSON |
| `POST` | `/admin/api/diagnostics/runs/{id}/baseline` | Mark as baseline |
| `POST` | `/admin/api/diagnostics/runs/{id}/evaluate` | Re-judge with current thresholds |
| `DELETE` | `/admin/api/diagnostics/runs/{id}` | Delete a run and its rows |
| `PUT` | `/admin/api/diagnostics/settings` | Retention + scheduled snapshot |

Reads ride the loopback-bypass middleware like other admin reads; start/stop/
delete ride the normal auth, matching the Machines tab's stance for its power
actions.

## Schema

`PRAGMA user_version` drives a forward-only migration ladder in `store.py`.
Never edit a shipped step — add the next one.

| Table | Holds |
| --- | --- |
| `runs` | One row per capture: machine, OS, window, trigger, status, baseline flag |
| `samples` | One row per tick: CPU (total + per-core), load avg, RAM, swap, disk, disk/net IO counters, GPU JSON, process count |
| `process_samples` | One row per process per tick: pid, ppid, name, cmdline, `app_id`, CPU, RSS, threads, status |
| `ports` | Listening sockets per tick, joined to owner + `app_id` |
| `verdicts` | The persisted health verdict per run |

IO counters are stored **raw and cumulative**; deltas are derived at read time.

## Caveats

- `psutil.net_connections()` needs elevated privileges on macOS to see other
  users' sockets; without them the port scan degrades to empty rather than
  failing the run.
- `psutil` reports per-process CPU relative to **one core**. The report layer
  divides an app's summed figure by the machine's core count (stored on the run,
  so an exported DB normalizes correctly on another machine) — so the "CPU"
  column reads as **percent of the whole machine** and is comparable to the
  resource envelope. Short measurement windows still make per-process CPU
  noisier than memory or process counts; treat it as indicative.
- Windows' **System Idle Process** (PID 0) is excluded from the inventory. It is
  a bookkeeping placeholder for idle cycles, and psutil reports its CPU as
  `ncores × idle-fraction` — ~1400% on a quiet 16-core box — so counting it made
  the idle process rank as the busiest thing on the machine. The exclusion
  matches on **name**, never PID 0, because macOS's PID 0 is `kernel_task`,
  which is real work. Sanity check after a capture: the sum of per-process CPU
  should land near the system-wide figure (10.9% vs 11.5% on the reference box).
- System CPU is measured over the sampler's **own** 0.5 s window rather than
  psutil's `interval=None` mode. That mode reports usage since the previous call
  *in the process*, and the hub's Hub-tab sampler already calls it every 2 s —
  whichever ran last stole the other's delta, which made diagnostics report
  0.0% CPU on a genuinely busy box.
- Captures are strictly local. Triggering a capture on a *peer* from this hub is
  deliberately not offered: each host's hub owns its own sampler, so start one
  from that machine's own `/admin`.
