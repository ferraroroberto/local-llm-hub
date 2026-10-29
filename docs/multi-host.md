# Multi-host topology

*As with [Active rotation](models-and-roles.md#active-rotation), host names in this
section (including the `hosts:` chain examples below) describe the
placement decisions as made — [`config/models.yaml`](../config/models.yaml)
alone is authoritative for current ownership.*

`local-llm-hub` runs as **one full install per machine**, but a model can
be *owned* by one host and made reachable through any other host's own
`base_url` — a client never needs to know or care which machine actually
runs a given model. Each `hosts:` entry gets an
`address:` (LAN IP) from `config/machines.local.yaml` (see **Machine identity**
in [admin-console.md](admin-console.md)), and each `models:` row in `config/models.yaml` gets an optional `host:` (which
host owns it — omitted means "whichever host resolves this config", i.e.
every existing single-host model is unaffected). A model listed in a
*non-owning* host's `enabled:` is transparently proxied: the request lands
on that hub exactly like any other, gets resolved, sees `host` doesn't
match the active machine, and is forwarded verbatim to the owning host's
own `:8000` (not its raw backend port) — so the proxied call still lands in
the owning hub's own observability ring. This is symmetric: the Mac Mini's
own hub can equally proxy to a Windows-owned model.

This powers the `mac-mini-m4` host (Apple M4; address in
`config/machines.local.yaml`):

- **`qwen3.5-9b`** — same `llama-server`, reachable transparently from any
  host via the proxy mechanism above regardless of which machine currently
  runs it.
- **`parakeet-tdt-0.6b-v3`** — NVIDIA Parakeet TDT 0.6B v3 (running the
  Parakeet Ultra checkpoint) on the Apple Neural Engine via [FluidAudio](https://github.com/FluidInference/FluidAudio)
  (CoreML), served by the vendored Swift worker in `mac/parakeet-worker/`
  + `src/parakeet_server.py`. Fills the **`audio_transcribe` role** on hosts
  that set it primary (a latency call, not an accuracy one): a plain
  `/v1/audio/transcriptions` call with no `model` lands here when it is, and
  fails over to whisper-turbo when parakeet is down. It is faster than
  whisper-turbo but drops the "Claude Code" wake phrase and mangles "YOLO"
  (a vocabulary-rescorer fix was disproven), so callers that need that
  jargon send `model=whisper` explicitly. Full measurement + trade-off
  writeup:
  [parakeet-asr-evaluation.md](parakeet-asr-evaluation.md).

The same cross-host proxy mechanism applies to any other satellite host
added to `config/machines.local.yaml` — whichever STT/TTS rows it owns
reach every other hub through the pattern above. voice-transcriber retains
its own local `:8090` escape-hatch spawner for transport failures.

A host's estimated VRAM footprint is the sum of its resident rows' `vram_mb`
(`config/models.yaml`) against its own ceiling. The fleet-placement
grid's own static estimate is lower by default: `startup: on_demand` rows
only join a host's sum while actually running, not as a standing
worst-case reservation — see [model-lifecycle.md](model-lifecycle.md).

Current host ownership for every row above — which machine runs what, and
any failover chain — is [`config/models.yaml`](../config/models.yaml)'s alone.

The admin UI's **Machines** tab shows every other hub-running peer's own hub
(mac-mini-m4, gaming — any future satellite with a non-empty `enabled:` list
appears automatically) in each machine's sheet. Cross-host auth reuses `extra_allowlist` in
`config/webapp_config.json` (per-machine, not committed) — each host's LAN
IP is allowlisted on the other, the same bypass the bearer-token middleware
already grants loopback callers.

### Mac Mini lifecycle: autostart, remote bootstrap, sync

The Mac Mini's hub has no tray-equivalent process supervisor — a
`~/Library/LaunchAgents/<label>.local-llm-hub.plist`
LaunchAgent fills that role instead (`RunAtLoad` + `KeepAlive`, installed
by `python -m src.install --fix` on the Mac itself — see
`mac/launchagent/`). A **deliberate** stop (`POST /admin/api/hub/stop`)
`launchctl bootout`s the job so it stays down; a restart
(`POST /admin/api/hub/restart`) uses `launchctl kickstart -k`. (macOS
detail worth knowing if you touch this: launchd respawns a job under
`KeepAlive` after *any* signal-terminated exit — a plain self-SIGTERM and
even `launchctl stop` both get relaunched — `bootout` is the only thing
that actually unloads it.)

For the case where the Mac's hub is fully dead (crashed before
`RunAtLoad` fires, or manually killed), Windows can bring it back over a
**dedicated, forced-command-restricted** SSH key
(`~/.ssh/local-llm-hub-remote-ctl`, path in `.env`'s
`LOCAL_LLM_HUB_SSH_KEY`) — the Mac's `authorized_keys` restricts that key
to `mac/bin/hub-remote-ctl.sh`, which only allows two verbs
(`bootstrap` / `sync`), no general shell. The Machines tab renders one row
per machine; each hub-running peer gets a **Wake** button (visible when unreachable →
`POST /admin/api/hosts/<host-id>/bootstrap`) and a **Sync** button (visible
when reachable → `.../sync`, which `git pull --ff-only`s that peer's
checkout before restarting it — the Linux dispatcher, `linux/bin/hub-remote-ctl.sh`,
gives `gaming` the same two verbs). An **out-of-sync** badge
appears on a peer's row when the two hubs' `git_sha` (from
`/admin/api/version`) differ. The sheet then says *why*, because `sync` only
moves a peer to `origin/main` and so closes just one of the causes.
`src/fleet/sync_diagnosis.py` resolves both served builds against this hub's own
checkout and `origin/main` (one `git fetch` per five minutes at most) and names
the case: this hub runs an older build than its checkout (restart this hub), this
hub's checkout is behind main (pull and restart here), the peer is behind main
(**Sync** is offered), the peer is ahead of main, or either side runs work that is
not on main (neither fixes it). A build id or `origin/main` that cannot be
resolved reads as *not confirmed*, never as a cause, and offers no Sync. After a
Sync the status is re-read and the toast reports whether the hubs now match.

Automatic peer wake/sync on the tower's own boot is not a separate
per-service toggle; it is owned entirely by the **fleet reconcile loop**
(see *Fleet placement* below): on boot,
the loop wakes + syncs + starts every peer that carries fleet placement. A peer
with **no** placement is deliberately left asleep (nothing to run means no
reason to wake it) — the manual **Wake**/**Sync** buttons above stay available
for that case, or place a model on it to have the loop take over. `git_sha`
currency of an already-running peer is surfaced by the out-of-sync badge and
fixed with the manual **Sync** button; the reconcile loop's own sync happens
along the wake/bootstrap path when a peer is brought up cold.

The Models tab tags every remote-owned tile with a small `on <host-id>`
badge (e.g. `qwen3.5-9b` / `parakeet-tdt-0.6b-v3` both show `on
mac-mini-m4`) so a displayed PID is never mistaken for a local process.

**Placement cards.** Each process-backed row in the
Models tab also renders its declared placement *intent* straight from
`config/models.yaml`: the host chain in priority order (effective owner
highlighted, cpu-resident tiers marked — the flag is the *effective* device
per host from `model_registry.cpu_resident_map`, so an always-CPU row like
`whisper-medium-translate`'s `-ng` is tagged on its owner too, matching the
memory summary) and the startup policy, read honestly from the live state
(*Always on*, *on demand · loaded*, *on demand · idle*). No per-card size chip
and no host budget bar: capacity is the memory glance's job. The data
rides `GET /admin/api/models` as a per-row `placement` object. The chips are not on the row
face: they head the row's menu and its sheet / detail pane.

**Models layout.** The tab opens on a **memory** glance — one meter per
machine (live GPU memory, RAM on a machine with no discrete GPU, the `~`
estimate against the declared ceiling only as the fallback, *not confirmed*
until a figure exists) — read on tab open and once after a Start / Stop
settles, never polled. Below it, **Running · All** picks the list (the old
*Active models only* switch's stored choice carries over), and one shared row
per model, grouped by the machine serving it: the avatar's alive badge says
it runs, the meta line carries kind, remote host, port and footprint, an
exception chip appears only for *no key*, *failover*, *adopted* or *on demand*,
and **Start / Stop** is the one visible verb. Everything else is in the row's
menu (Ping, Transcription dictionary, Edit placement, Model log, and Force
stop last, behind a confirm). Tapping a row opens the model's sheet on a phone,
or fills the detail pane beside the list on a wide window (1100px+, mouse):
full details (process id, device, aliases, the roles it serves), placement,
the placement editor or dictionary, and the log tail. Tapping a memory row
opens that machine's running / loadable summary the same way.

**Editable placement — the UI writes through to git.** On the single
write host (`hub.config_write_host` in `models.yaml` — the tower) each
model's menu offers **Edit placement**, which opens the editor in the
model's sheet (detail pane on a wide window): reorder /
add / remove chain hosts from the fleet host list, toggle a host's degraded
`cpu` tier, flip `eager` ↔ `on_demand`, set `idle_unload_minutes`. Saving
`PUT`s `/admin/api/models/<id>/placement`, which **validates first** —
schema (known hosts, no duplicates, every chain member cross-lists the model
in its `enabled:`) plus the VRAM budget as a *hard* gate: an edit whose
eager, non-`cpu` chain members overcommit a host's `vram_mb` ceiling is
rejected with the arithmetic inline, before any file change. An accepted
edit mutates `config/models.yaml` comment-preservingly (ruamel.yaml
round-trip — the file's comments survive byte-for-byte), commits as
`config: <model> placement via admin UI` under the `local-llm-hub
config-bot` author name, and pushes to `origin main`. The write refuses a
dirty tracked file or a non-`main` checkout (409), and a failed push rolls
the commit *and* the file back (502) — never a silent local-only divergence.
Satellites converge automatically: a successful push fires the sync
(git pull + hub restart) at every hub peer, and a periodic drift loop on the
write host re-syncs any satellite whose `models.yaml` sha lags (e.g. it was
powered off during the push; it also catches up on next boot). The Models
card header shows the config version (`Config <sha>` — the models.yaml HEAD
sha, also on `/admin/api/version` as `config_sha`), so drift between hubs is
visible by comparing their `/admin` pages. Non-write hosts render the cards
read-only and 403 the write endpoint. The write runs no test suite, so the
suite cannot depend on what it edits: every unit test reads `models.yaml`
with the four placement keys (`host`/`hosts`/`startup`/`idle_unload_minutes`)
replaced by the table in `tests/_placement_fixture.py`. A
placement edit is a routing decision, never a red `main`.

### Fleet placement: registry-derived desired state

The per-machine service autostart (`startup_profile.json`, see [running.md](running.md)) answers
*"which services should this box bring up when it boots?"*. **Fleet
placement** answers the fleet-wide model question — *"what should run on
which machine, kept true even as boxes reboot or models die?"* — and it is **derived from `config/models.yaml` itself**: a `startup: eager`
row's desired host is the first member of its `hosts:` chain (or its bare
`host:`) that enables it; `startup: on_demand` rows are never in the desired
set (the first request loads them, `src/on_demand.py` unloads them idle).
There is no separate placement file to keep in sync: the registry is
the single source. Moving a model is a `models.yaml` edit
(the Models-tab placement cards / swap-model), which the config-write
pipeline commits and syncs to every satellite.

A background **reconcile loop** (`src/fleet/fleet_reconcile.py`, wired in
`server_lifecycle.py`) converges the fleet to the derived state on boot and
every ~5 min (`LOCAL_LLM_HUB_FLEET_RECONCILE_INTERVAL_S`). For each host with
desired models it: wakes an unreachable `can_ssh` satellite (same
forced-command `bootstrap` as above) and starts any desired model not already
running. A satellite that was powered off applies its own set on power-up
with no tower involvement — its hub derives the same desired set from its own
synced `models.yaml` (`model_registry.desired_model_ids`) at startup. The
loop leans entirely on existing idempotency — `backend_process.start` adopts
a live port and a forwarded `/start` returns a benign `409` — so the periodic
pass is safe to repeat forever and is strictly **additive**: it never stops a
model you started by hand.

Each machine's **memory summary** on the Models tab (opened from its memory row) is read-only, over
**every** configured fleet host: the models **running** there right now (live state), the models
**loadable** but not running (that host's chain members, including
`on_demand` rows) — both lists one model per line, right-aligned at one
muted caption size, keeping the per-model device tag — and a
**homogeneous capacity line** (`GPU <used> / <total> · RAM <used> / <total>
GB`, the same shape on every machine). The capacity figures are **live-first**
: GPU and RAM used/total come from the same probes the Machines tab
reads (local psutil/nvidia-smi, the cached SSH stats probe on peers), at the
same 1-decimal format, so the two tabs can never disagree while both are
live; the `~`-prefixed `est_vram_mb` sum vs the declared ceiling appears
only as a fallback where no live GPU figure exists (host offline, or a
unified-memory host with no discrete-GPU metric). It
carries **zero controls** — placement is edited per model or in
`config/models.yaml`. Liveness is the same
hub-independent TCP probe the Machines tab uses (*is the box on?*), so a
**managed-only satellite that runs no hub** (`openclaw` — driven directly
over SSH, no models registered) reads *online* honestly with a note instead
of an empty list. A live backend the hub merely *adopted* on a mutex-shared
port (voice-transcriber's own whisper-server on `:8090`) is labelled
`· external` rather than claimed as hub-run.

**Capacity awareness:** each host row may declare a `vram_mb` GPU-VRAM
ceiling (and, display-only, a `ram_mb` total) and each GPU model a rough
`est_vram_mb` footprint (all in `config/models.yaml`). The summary sums a host's
desired + running models' `est_vram_mb` and shows an **advisory** *"Over VRAM
capacity"* warning (its memory meter in danger, the header line naming it) when the total exceeds its ceiling — a
heads-up that a placement change overcommits the box (e.g. `gaming`'s 8 GB
GTX 1070), replacing the by-hand `nvidia-smi` glance. Rows resident on
**CPU on that host** — piper's CPU-only engine, `-ng` whisper rows, a chain's
degraded `cpu: true` tier — hold no GPU VRAM and are **excluded from the sum**
(`model_registry.cpu_resident_map()`); the same exclusion applies to
the model cards' effective-device pill tags and the on-demand loader's
headroom check, so every consumer inherits it. Hosts with no `vram_mb`
(Apple-silicon unified memory, managed-only boxes) never warn — on the
capacity line their GPU denominator is the system-RAM total (on unified
memory the GPU pool *is* system RAM; display context only, never a warning
input). Each per-host status object in the `GET` response below therefore
also carries `vram_mb` (ceiling or `null`), `est_vram_mb` (the summed,
CPU-excluded desired/running footprint), `capacity_warning` (bool), `ram_mb`
(declared total or `null`), `ram` (the live `{used_gb, total_gb,
percent}` snapshot: local psutil on the hub host, the Machines tab's cached
SSH stats probe on reachable peers, `null` where no live figure exists, in
which case the UI shows the declared total), and `gpu` (the live
`{used_mb, total_mb}` snapshot from the same plumbing: local nvidia-smi,
the cached SSH probe on peers, `null` where the host reports no GPU metric,
in which case the UI falls back to the `~` estimate vs the ceiling). The
`est_vram_mb` figures remain static engineering approximations feeding the
advisory warning and the no-live-figure fallback only. Each **model card's
meta line** ends with the same `· ~X GB` static footprint — shown
only for rows that actually load a model process; virtual aliases sharing
another row's process and CPU/ANE rows show nothing.

Or drive the same API directly:

```bash
# See the derived placement + live per-host status (eligible / reachable / running)
curl -s http://127.0.0.1:8000/admin/api/fleet-placement | jq

# Force a convergence pass on demand (the loop already does this periodically)
curl -s -X POST http://127.0.0.1:8000/admin/api/fleet-placement/reconcile
```

The reconcile loop owns all peer wake/sync uniformly (mac-mini,
gaming, and any future satellite); the registry it converges on is
the single cross-host source of placement truth — a model desired on a
machine that's powered *off* is simply started when the box next reports in
(or by the box itself, from its own registry, when it boots).

### Dynamic model fallback: ordered host chains

Fleet placement above answers *"what should run where"*; **dynamic
fallback** answers *"and what if that host is down?"*. A `models:` row may
replace its single `host:` with an ordered preference chain:

```yaml
whisper:
  hosts: [gaming, mac-mini-m4, {id: tower, cpu: true}]
```

A bare `host:` stays valid and is exactly a one-element chain — every
existing row behaves as before, and with no multi-host chain configured
the engine adds zero probes and zero background work.

- **Ownership resolution.** The model's *effective owner* is the first
  chain candidate whose hub is reachable and that lists the model in its
  `enabled:`. Every dispatch path (chat, audio, admin start/stop/log)
  resolves against the effective owner and proxies there over the existing
  cross-host path — chain order is the deterministic tie-break, so
  two live candidates always agree on who owns routing.
- **Failover.** A background loop in each hub probes the chain hosts
  (reusing the Machines-tab `peer_health` prober — no second prober,
  tailnet fallback included) every `probe_interval_s` (30 s). When
  the owner stays *continuously* unreachable past `fail_after_s` (90 s),
  ownership moves to the next reachable candidate — and that candidate's
  own hub starts the model locally (each host acts only on itself; no
  master required, so failover works even when the control node is the
  host that died).
- **Degraded CPU tier.** A chain entry flagged `cpu: true` runs the model
  CPU-offloaded on that host — the hub rewrites its launch args
  (llama-server `-ngl 0`, whisper `-ng`, tts `--device cpu`) so the last
  resort is "up but slower," not "must match GPU perf."
- **Failback with hysteresis (anti-flap).** When a more-preferred host
  returns it must stay up *continuously* for `failback_after_s` (10 min)
  before ownership hands back; a repeatedly-rebooting host never
  accumulates the window, so ownership cannot bounce. The fallback host
  then stops only the instance the engine itself started (hand-started
  processes are never touched — same additive contract as the reconcile
  loop). `policy: sticky` disables automatic hand-back entirely.
- **Tunables.** Top-level `failover:` block in `config/models.yaml`
  (`probe_interval_s` / `fail_after_s` / `failback_after_s` / `policy`);
  defaults documented inline there.
- **Maintenance gate.** The fleet reconcile loop's own always-on
  convergence races this engine's `fail_after_s` window — reconcile can
  SSH-resurrect a deliberately-stopped peer within seconds, before failover
  ever observes a continuous outage. `src/fleet/fleet_maintenance.py` gives the
  tower a host-scoped drain marker reconcile honours
  (`GET/POST/DELETE /admin/api/fleet-maintenance/{host_id}`), for running a
  drill without editing `.env`. See [fleet-maintenance.md](fleet-maintenance.md).

**Enabling a chain:** every chain member must cross-list the model in its
`enabled:` **and** pre-stage the weights (`python -m src.install --fix` on
that host — the installer treats chain members as local candidates). The
Models tab tags a model served off-preference with a
`failover (prefers <host>)` note on its tile.

**The live chains.** A row opts into failover with `hosts: [...]` instead of
a bare `host:` — the first entry is primary, the rest are fallbacks tried in
order, and `{id: <host>, cpu: true}` flags a degraded-but-up CPU tier rather
than a compromise. Which rows currently carry a `hosts:` chain, and in what
order, is [`config/models.yaml`](../config/models.yaml)'s alone to say —
never restated here.

