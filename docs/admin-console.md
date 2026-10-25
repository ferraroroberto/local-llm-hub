# Admin console

## Hub tab

The **Hub** tab is where the admin lands, and it answers *is the hub serving,
and is anything failing?* first:

- **Glance.** Requests, errors and median latency **since hub start** (the
  hub keeps no hourly history, so the card names its window; a failed read
  says *not confirmed*, never zero), one load sparkline whose series a
  CPU · GPU · RAM · VRAM control swaps (each with its current figure), and
  the process id and uptime.
- **Services as exceptions.** Docker, Langfuse and AgentsView fold into one
  row while all are up; a stopped or failing one is its own row with
  **Start**, and the header line names it. The row opens the **Services**
  sheet: every service with Start / Stop and its detail, **Launch Docker +
  Langfuse**, and the **Langfuse** sheet with the Telemetry export state, Hide personal data,
  endpoint, stack health, **Open Langfuse UI** and the offline setup hint.
- **Activity.** One feed of request rows (model first, status and latency,
  backend · tokens · detail, clock last), **Requests · Errors**, with a
  filter. A row with a trace opens its sheet: the prompt and completion
  preview (or the offline fallback), a thumbs up / down score and **Open in
  Langfuse**. **By model** opens the per-backend counters with their summary
  line; **Server log** opens the live tail with Pause. The ⓘ explains the
  window and the audio calls that bypass the hub.

On a wide window (1100px+, mouse) the glance and services sit left of the
Activity feed; a phone shows one column. The model roles and placement live
on the Models tab.

## Settings

The gear in every page header opens **Settings**, the fleet's inset
groups: **Display** (text size, and theme: System · Light · Dark beside the
header toggle), **Startup** (the services that come up with the hub, saved as
each switch flips), **Health** (Health & install with Fix all and Re-check, run
when the row opens; a failing check also reaches Hub's header line) and
**About** (build, models.yaml config version, process id, Open Langfuse UI,
the frontier report). A row with a body opens it in place on a phone and
beside the list on a wide window. Telemetry lives in Hub (Langfuse sheet,
Activity, By model) and Code (Sub-agents (telemetry)), and a `#telemetry` link
opens Hub Activity.

## Usage log

The admin UI records which views and controls get used, and on which device 
class, so a design review reads real frequency (the vendored 
`usage-log`). Events are only the kind (`session_start`, `view`, `tap`), a view 
id (the tab), a control id (a constant `data-track` in `index.html`), a 
per-visit random session id, device class and timestamps; never content, 
names, queries, URLs or the user-agent. They post to the hub's own 
`/admin/api/usage/events` (behind the same bearer gate, so a phone sends the 
token and the desktop on loopback does not need one) and land in 
`usage.sqlite3` under the runtime-data dir (`LLM_HUB_USAGE_DB_PATH` 
overrides it). Nothing leaves the machine.

## Machines console

The **Machines** tab turns the hub into a fleet machine console — one place
to see the health of every box and act on it. It reads the host inventory
from `config/models.yaml` `hosts:` (two managed-only machines
alongside `tower` and `mac-mini-m4`: **OpenClaw**, a Linux laptop, and
**gaming**, a Ryzen Linux inference satellite) and renders one row per machine.

**Layout.** Each row answers *is it up?* from its face: the avatar's
corner badge (alive or down), the uptime, and an exception chip only when
something is wrong (*hub down*, *out of sync*, *via tailnet*, *flaky link*).
A down machine with a MAC shows a **Wake** (power) icon-button on the row; a reachable machine whose
own hub is down shows **Start hub**. Tapping a row opens the machine's sheet on
a phone, or fills the detail pane beside the list on a wide window (1100px+,
mouse): the gauges, **Terminal** and **Remote**, the **Connection** row, the
machine's own **Hub** (state, both builds when they differ, **Sync** or
**Start hub**), then **Reboot** and **Shut down** last, behind a confirm. The
header line names *N offline* or the stale age, else the machine count, and
**Diagnostics** is its own row under the list (this machine only). The shared
row, chip, header line, ⓘ info sheet and wide-window primitives live in
`app_web/static/rows.js` / `rows.css` for the other tabs to adopt.

Every reachable machine shows the **same** snapshot — CPU / RAM / GPU / disk
(as uniform horizontal gauges) + uptime:

- **This machine** reads it locally from `src/system_stats.py`.
- **Peers** are probed two ways, both independent of whether the hub runs
  there — the card answers *is the box on?*, not *is the hub up?*
  (`src/fleet/remote_stats.py`): a **hub-independent TCP liveness probe** for
  up/down, and the same CPU/RAM/GPU/disk/uptime snapshot collected over the
  hub user's **own** passwordless SSH (a read-only one-liner, per-OS). A node
  flagged `dormant` (see **Dormant machines** below) is never SSH'd or
  hub-probed. All peer actions —
  read-only observability *and* reboot/shutdown — go over that general SSH
  (plus TCP for liveness); the forced-command key is reserved for the
  hub-lifecycle `bootstrap`/`sync` on the model-serving peers
  (Mac Mini and `gaming`, each running its own dispatcher script).

**Tailscale fallback.** Every peer-connect path — the model-proxy
upstream, SSH ops, and the remote stats/liveness probes — dials the peer's
wired LAN `address:` first and falls back to its `tailscale:` magic-DNS name
when the LAN path stops answering (`remote_stats.dial_address`, a short-TTL
last-known-good resolver). The wired NICs carry fixed DHCP reservations while
Wi-Fi is an *unreserved* fallback, so a wired failure moves a box to a pool
address — only the NIC-independent tailnet name survives that. The failover is
logged at info level (`falling back to tailnet …`) and the machine's row shows
a **via tailnet** chip while a peer is reached that way; hosts with no
`tailscale:` recorded behave exactly as before, and a healthy LAN path is
always preferred (no WireGuard hop while the wire works).

**Connection status.** Every machine's sheet — including the
active hub host's own — carries a **Connection** row that opens to **Address**
(the config `address:`, or a live `lan_ip()` lookup for the host's own row),
**MAC**, the network and the tailnet state. The network line shows *live*
connection detail read over the same SSH round-trip as the CPU/RAM/GPU/disk
snapshot — no extra probe: **Wired** vs **Wi-Fi · SSID · signal (dBm)** for
whichever interface currently owns the box's outbound default route, plus
the Tailscale state and a **Flaky link** flag
when the liveness probe that produced the row needed a warm-up retry
to answer. Every signal degrades independently (no `iw` on Linux still
reports Wired/Wi-Fi, just without SSID/signal; a down peer shows none of it)
— see [machines.md](machines.md) for the per-platform availability matrix.

**Reboot / shutdown (destructive, peers only).** Any peer with an SSH channel
(`address` + `ssh_user`) offers **Reboot** and **Shut down** actions; the
active hub host is always excluded (powering it off would take the console
down with it). These run over the hub
user's **own general SSH** — the same passwordless channel the
stats snapshot uses — as `ssh <user>@<host> "sudo -n /sbin/shutdown -r|-h
now"`, detached with `nohup` so the SSH command returns cleanly before the box
drops off. The only prerequisite is the peer's passwordless-sudo sudoers
drop-in (already in place on the Mac Mini and OpenClaw); **nothing has to be
deployed to a managed machine** — no per-peer key, no forced-command script.
This is why OpenClaw's power buttons work the moment it is reachable.

## Machine identity — `config/machines.local.yaml`

This repository is **public**, so it deliberately records nothing that maps a
private network. `config/models.yaml` carries what a reader needs to understand
the fleet — roles, model rosters, capacity ceilings — while the per-host
addressing lives in a gitignored sibling:

| Field | What it is |
| --- | --- |
| `address` | LAN address other hosts dial to reach this box's own hub |
| `tailscale` | Tailscale magic-DNS name — the peer-connect fallback |
| `mac` | wired-NIC MAC for Wake-on-LAN |
| `ssh_user` | login for the remote-bootstrap/sync endpoints |
| `rdp` | `{address, user}` for the Remote-Desktop launch action |

`src/host_profile.py` merges these onto the matching `hosts:` rows at load time.
To set it up, copy the committed template and fill in your own values:

```powershell
copy config\machines.local.example.yaml config\machines.local.yaml
```

The file is **optional**. Without it the hub boots normally and every
peer-dependent feature — proxy failover, remote stats, SSH ops, Wake-on-LAN,
RDP — simply stays inert, which is the correct behaviour for a single-machine
install. Restart the hub to pick up an edit, same as `models.yaml`.

**Dormant machines.** A machine that is off on purpose (broken, unused
for months) would otherwise sit in the Machines list as a red *offline* forever.
Mark it **dormant** in **Settings › Machines**: one switch per peer, saved at
once to `dormant: true` under that host in `config/machines.local.yaml` (this
machine only, never committed; the machine the hub runs on has no switch).
Switching it off again removes the key. A dormant machine is a neutral row
sorted after the live ones, never counted in the header's *N offline*, and no
automated path reaches it: failover chains and placement skip it (its models
go to the next chain member, with no *failover* flag), the fleet reconcile loop
sends it no Wake-on-LAN or bootstrap, and the sync fan-out, drift loop and
peer probes leave it alone. The one thing the Machines tab does is a cached
TCP connect, so a dormant box that comes back (after **Wake**, say) reads as
*Reachable · still dormant* with a hint to switch dormant off; the flag never
flips by itself. `dormant: true` can still be hand-set on a `hosts:` row in
`models.yaml`, and an overlay value (`true` or `false`) wins over it. The flag
is per hub: another hub in the fleet keeps its own `machines.local.yaml`.

**Wake (non-destructive).** A machine that is **down or dormant** and
has a wired-NIC `mac:` recorded in `config/machines.local.yaml` offers a
**Wake** verb on its row — a fire-and-forget Wake-on-LAN magic packet
(`src/fleet/wake_on_lan.py`, UDP broadcast to `255.255.255.255:9`, stdlib socket
only) via `POST /admin/api/machines/{id}/wake`. No confirmation loop: the
verb disables (its tooltip reads *Rechecking …*) and the next status poll reports whether the box
came up. The hub can't wake its own host (that's BIOS AC-restore territory),
and a host without a `mac:` simply has no Wake action. Per-machine WOL state
and caveats (Apple-silicon wakes from sleep only; openclaw has no wired NIC)
live in [machines.md](machines.md). home-automation's UPS orchestration consumes this
endpoint over loopback for deliberate remote power-on.

**Remote Desktop.** A per-machine **Remote Desktop** action serves a
generated `.rdp` launcher (built from the machine's configured `rdp`
`{address, user}` target) that the viewing device downloads and opens — no
web RDP client, no dependency on any out-of-repo launcher file.

**On-demand diagnostics.** The *this machine* card carries a **🔬
Diagnostics** row — a state chip showing the last health verdict (or live
capture progress) that opens a drill-in dialog. From there you can take a
**one-shot snapshot** or run a **timed capture** (15 min → 8 h, sampling every
5–60 s) that records system CPU/RAM/swap/disk/net/GPU **plus a full per-process
inventory and the listening-port map** into `data/diagnostics.db`.

The point is interpretation, not just recording: processes are attributed to the
app that owns them (`app-launcher: 3 procs / 800 MB`, not `python.exe ×14`) via
the committed `config/diagnostics_apps.json`; a finished run gets a persisted
`healthy`/`warning`/`critical` verdict from the tunable thresholds in
`config/diagnostics_rules.json`; and any run can be marked a **baseline** so
later runs report drift ("+2 resident apps, +3.1 GB idle RAM, new listener
:8099"). Each run also records **what it could and couldn't measure**:
where the hub lacks privilege — macOS denies socket enumeration and ~40% of
per-process memory/CPU — the report says "not collected" and the verdict reads
`HEALTHY · ⚠ partial coverage` rather than letting an unmeasured signal pass as a
clean bill of health. Deep analysis happens outside the UI — export a run as
JSON, download an LLM-ready markdown health report, or query the SQLite file
directly.

**It adds no resident process**: the sampler is an asyncio task inside the
already-running hub, so nothing exists when no capture is active. An **opt-in
daily snapshot** (default off) keeps multi-week trends alive without adding one
either. Pure `psutil` + stdlib `sqlite3`, so the identical capture runs on the
Mac Mini and OpenClaw.

**Hub-less machines are covered too.** A box that runs no hub (`openclaw`,
a dormant `tower`) is measured with a **zero-install** path: `scripts/portable_capture.py`
is a standalone `psutil`-only sampler delivered over SSH — `ssh host "python3 -
--duration-s 3600" < scripts/portable_capture.py > out.json` — whose raw output
is replayed into this store with `python -m src.diagnostics.ingest out.json`
(also `POST /admin/api/diagnostics/ingest`). The portable script interprets
nothing; attribution, coverage, and the verdict all run centrally at ingest
against the *source* machine's OS, so an ingested `openclaw` run is
indistinguishable from a local one and editing the attribution config
re-attributes it with no change on the peer. Machines that run their own hub use
the native path; both converge on the same store. Full reference:
[diagnostics.md](diagnostics.md).

**In-browser SSH terminal.** The **Terminal** action opens an xterm SSH
session by **reusing app-launcher's session-host** (its loopback ConPTY/WS
engine) rather than rebuilding a PTY stack here. This needs
app-launcher to have an `ssh` agent registered; without it the terminal
degrades gracefully with an actionable "unavailable" state. Everything on this tab rides the existing bearer-token /
loopback-bypass middleware — no new auth scheme; the access model stays
loopback / Tailscale only.

