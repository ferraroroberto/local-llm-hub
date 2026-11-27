# Machine health watch

A machine that runs for weeks without a reboot fails slowly: one process leaks handles or memory for days until the whole box is short of a resource. The health watch is the hub's early warning for that. Every hub samples its own machine on a slow cadence, keeps a rolling history, and answers one question: **is anything leaking or under pressure, and is restarting one process enough, or does the machine need a reboot?**

It is read-only. It never kills, restarts or closes a process and never reboots a machine on its own; the fix it names is advice, and the one action it leads to (Reboot in the Machines row menu) is the owner's tap behind a confirm.

## Where it shows

- **Machines tab**, every row whose machine samples itself: this machine, and each peer that runs its own hub (the hub reads the peer's `GET /admin/api/machines/health`, cached for a minute). An exception chip (*needs action*, *health watch*, *health not confirmed*) closes the row's meta line, the header line counts the machines that need action or are on watch, and the sheet's **Health** section lists the verdict, every finding and the suggested fix. A healthy machine carries no chip. A peer whose verdict could not be read (an older build, a timeout) shows *health not confirmed*, never fine.
- **The row menu** (the row's kebab) repeats the verdict and fix in its header and offers **Reboot**, the one fix the hub can carry out for a machine, behind the confirm dialog. Restarting a single process stays advice: the hub holds no scoped way to restart a process on another machine. Reboot is disabled, with the reason on its hint, on the machine the hub runs on (the hub refuses to power-cycle its own host), on an unreachable machine, and while the data is stale or an action is in flight.
- **`GET /admin/api/machines/health`**: the full verdict for the launcher and other readers. It is served from memory, so it is cheap to poll.

| Field | Meaning |
| --- | --- |
| `verdict` | `ok`, `watch`, `act` or `unknown` |
| `reason` | The worst finding in one line, with a count of the rest |
| `fix` | The suggested fix for the worst level, a process restart preferred over a reboot; `null` when nothing is wrong |
| `findings` | Every finding: `level`, `kind` (`process`, `system`, `probe`), `metric`, `value`, `reason`, `fix`; process findings add `process`, `pid`, `rate_per_hour`, `eta_hours` |
| `offenders` | The top three processes per metric with their value and growth per hour; `ports` lists the top port holders (see [Port watch](#port-watch)) |
| `system` | The latest system counters (commit, RAM, pools, handles, TIME_WAIT, ephemeral ports, disk, uptime) |
| `coverage` | Each probe's state: `ok`, `failed`, `denied`, `unsupported` |
| `alerts` | The last 20 act and cleared events |
| `ports` | The port watch's block: current and worst-in-the-last-hour percent per protocol, TIME_WAIT, top holders, the last exhaustion event (see [Port watch](#port-watch)) |
| `sampled_at`, `sample_ms`, `samples`, `interval_s`, `enabled`, `host_id` | When, how long the sample took, history depth, cadence |

## Verdict

Levels rank `ok` < `unknown` < `watch` < `act`. A known problem outranks a blind spot, and a blind spot is never `ok`.

- **`unknown`**: no sample yet, the last sample is older than `stale_after_intervals` × `interval_s`, or a probe that should work here **failed**. A probe the platform withholds (`denied`, such as TCP connections for an unprivileged user on macOS) or does not have (`unsupported`, such as commit charge off Windows) is listed in `coverage` but does not hold the verdict at `unknown`; otherwise a Mac would never read anything else.
- **`watch` / `act`**: the worst finding's level.

### Process leaks

For each of the top `top_n` processes by each metric:

| Metric | Windows | macOS / Linux |
| --- | --- | --- |
| `handles` | kernel handles | open file descriptors |
| `memory_mb` | private bytes | resident set |
| `gdi`, `user` | GDI and USER objects (per-process limit 10,000) | not measured |
| `threads` | threads | threads |

A process finding fires on either test, whichever is worse:

- **Absolute**: the value is past `watch_value` or `act_value`.
- **Growth**: the series over the last `leak.window_hours` is steady (at least `min_samples` samples over at least `min_span_hours`, at least `min_rising_fraction` of the steps rising, and a net gain), the value is at least `rate_min_value`, and the least-squares growth per hour is past `watch_rate_per_hour` or `act_rate_per_hour`. The reason names the process, the value, the rate and the hours left until `act_value`.

A process is tracked by PID and creation time, so a restarted process starts a new series and an exited one drops out at once. That is what clears a finding. `memory_mb` has no absolute limit, because a model server legitimately holds tens of GB; only steady growth counts, and a one-off step up (a model load) is never steady.

### System pressure

| Metric | Default watch / act | Fix when it fires |
| --- | --- | --- |
| `commit_percent` (commit charge as % of the limit) | 85 / 95 | Restart the process holding at least `attribution_share` of the commit, else reboot |
| `ram_available_percent` (low is bad) | 10 / 5 | Restart that process, else close the largest memory users |
| `nonpaged_pool_percent`, `paged_pool_percent` (% of RAM) | 6 / 10, 15 / 25 | Reboot: kernel pool is only returned by restarting Windows |
| `total_handles` | 2 M / 4 M | Restart the process holding at least `attribution_share` of all handles, else reboot |
| `tcp_time_wait` | 4,000 / 10,000 | No reboot: TIME_WAIT drains in minutes; find the burst (`fleet-config`'s port-exhaustion runbook) |
| `ephemeral_ports_percent` (local ports in use within the dynamic range) | 50 / 80 | Same as TIME_WAIT |

The two port rows are this slow sample's backstop. While the port watch has a fresh sample its findings replace them, because a 10-minute sample misses a burst that drains in minutes.
| `disk_free_percent` (OS drive, low is bad) | 15 / 5 | Free space on the system disk |

A process in `reboot_only_processes` (`System`, `csrss.exe`, …) cannot be restarted on its own, so its fix is a reboot.

The defaults are calibrated on a real case on the tower: one desktop process grew about 90,000 handles an hour to 3.9 M in about 44 hours and pushed the system total to 4.56 M, with commit and RAM fine. The watch reads that as `act` within half an hour of the growth starting (the growth rule needs four samples), and the fix it names is restarting that process, never a reboot. Closing the process brought the total back to 620 k, which is `ok`.

## Alerts

The alert is the hub log, visible in the Hub tab's live log: one warning when the verdict enters `act` (`🚨 health watch: act: <reason>. Suggested: <fix>`) and one info line when it leaves (`✅ health watch: cleared`). The pair fires once per episode, however many samples stay at `act`, and both land in `alerts` for the endpoint's readers.

## Cost

The sampler runs in a worker thread every `interval_s` (600 s), never on the event loop. On Windows the whole process table comes from one `NtQuerySystemInformation` call and the system counters from one `GetPerformanceInfo` call; psutil's per-process reads took about 8 s for about 600 processes on the tower and are not used there. Measured on the tower (about 640 processes, 800 TCP connections): **about 30 ms per sample**, plus about 70 ms once per hub start to read the dynamic port range from `netsh`. Each sample's own time is in `sample_ms`. The history keeps only the top processes per metric for `history_hours`, about 150 samples.

## Configuration

`config/health_watch.json` (committed) holds every threshold and the cadence. It is re-read on every sample, so a retune needs no hub restart, and it is merged over the built-in defaults, so a partial file only overrides what it names. `"enabled": false` stops the watch; the Machines tab then shows nothing for it. `null` disables any single limit.

## Port watch

Ephemeral-port exhaustion builds and drains in minutes, and every fleet web app fails at once when it happens. A 10-minute sample falls between bursts, so a third loop samples ports every `ports.interval_s` (30 s) and adds its findings to the health verdict. The diagnosis runbook is `fleet-config`'s `docs/recurring-gotchas.md` (port exhaustion); this watch is the detector, never the fix. It never closes a socket, stops a process or changes a machine setting: `TcpTimedWaitDelay` and the dynamic range are the owner's call.

### What it reads

- **Ports in use per protocol**: the distinct local ports inside each protocol's dynamic range that a TCP or UDP endpoint holds, as a percent of the range. The range is read from the OS once per hub start (`netsh int ipv4 show dynamicport tcp|udp` on Windows, `/proc/sys/net/ipv4/ip_local_port_range` on Linux); 49152-65535 stands in only when it cannot be read. UDP is counted on its own, because a UDP exhaustion leaves TCP untouched.
- **TCP TIME_WAIT** sockets. They have no owning process, so they count toward the TCP percent but never toward a holder.
- **Holders**: the `top_n` processes by endpoints held in the dynamic ranges, split TCP / UDP.
- **Exhaustion events**: the System log's Tcpip 4227 (a local port still in TIME_WAIT could not be reused: connection churn, not exhaustion), 4231 (TCP ran out of ephemeral ports) and 4266 (UDP ran out), read with `wevtutil` every `events_interval_s` (60 s) over a window that overlaps the previous read; the record id stops an event being counted twice. Windows writes these the moment an allocation fails, so they are ground truth even when no sample landed in the burst. Off Windows the event probe is `unsupported`.

### The holder snapshot

A burst drains within a minute, long before the next 30 s sample, so by the time the event log is read the holders are gone. When a read finds a **new** `4231` or `4266` (a `4227` and a re-read of a known event do not), the watch takes one extra port sample at once and keeps it on the event, in SQLite, so it survives a restart. Besides the top holders by live endpoints it attributes **TIME_WAIT** sockets, which have no owning process: each is grouped by the local server port it belongs to (its own port, or the remote port it points at) and mapped to the process listening on that port. A group whose port has no local listener says so. Only ports, process names, a short label and counts are kept, never addresses or command lines (see [Holder labels](#holder-labels)).

The snapshot runs a few seconds after the event, never at it, so the verdict says how many (`snapshot 9 s after the event: TIME_WAIT mostly port 8000 (hub.exe, 60 sockets); top holders ...`). A snapshot that found nothing or could not read a table reads `not confirmed`, never "nothing held them"; an event with no snapshot (stored before this existed, or the read failed) says the holder is not confirmed. The finding carries the raw `snapshot`, and `ports.last_event` carries it with a `snapshot_summary`.

### The burst window

A UDP socket frees its port the instant it closes, so a burst can be gone before the snapshot looks. A second loop therefore tallies the endpoints each process holds in the dynamic ranges into a `window_s` (60 s) in-memory buffer: every `window_interval_s` (1 s) while the newest port sample holds at least `window_arm_percent` (25 %) of either range, or while no fresh, readable sample says otherwise, and every `window_idle_interval_s` (10 s) below that. A burst builds over the 30 s samples before it reaches the 80 % act line, so the 1 s tally arms in time without reading both tables every second on a calm machine (`src/health/port_window.py`, in a worker thread, never on the event loop). When a **new** `4231` or `4266` is read, the watch stores the window's peak holders of the event's protocol on the event, beside its snapshot: for each process the highest count, the second it was reached, and its lowest count in the window (`low`, 0 when it was absent). The tallies themselves are never persisted.

The holder is **confirmed** only when the window reaches back to the event and some process rose by `window_confirm_percent` (25 %) of the protocol's range or more (4,096 endpoints of a 16,384-port range); a flat block (a vendor service holding 50 UDP ports all day) and a small mover (a process that went from 5 to 83 ports) are listed but never named as the burst, the event text giving the largest rise (`largest rise 78 ports, too small to exhaust the range`). An empty or unreadable window, one that starts after the event, or one in which no process rose far enough reads `peak holder not confirmed`, never an all-clear. The limit: a burst shorter than the tally interval (1 s armed, 10 s idle) can fall between two tallies, and an event read more than `window_s` after it was logged is no longer covered; the event text says so. A snapshot ranks its holders by the event's own protocol (a UDP event by UDP endpoints, a TCP event by TCP), not by TCP plus UDP.

### The sub-second trace

The snapshot, the window and the event log all poll, so a burst of sockets that are bound and closed without connecting, shorter than one tally, escapes them: it leaves no TIME_WAIT behind, and a UDP port frees the instant its socket closes. Only a kernel trace with process ids can name it. Starting that trace needs an elevated shell and runs continuously, so it is the owner's machine-level step; the hub only reads what it saved and never starts, stops or flushes it.

Arm it once, in an elevated PowerShell, from the root of the primary checkout (the save task runs this script by its path, so never arm from a worktree that will be removed):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\port_trace.ps1 -Arm
```

`scripts/port_trace.ps1 -Arm` starts the circular ETW session `LlmHubPortTrace`, capped at 512 MB (`-MaxMB`): the Winsock AFD provider (keyword `0x4`: socket create, bind and close, TCP and UDP alike, plus every send and receive, which is why the cap matters) and the TCPIP provider (keywords `0x8` bind and `0x80` diagnosis: port reservations). It registers the task `LlmHubPortTraceSave`, which runs as SYSTEM on every Tcpip 4231 or 4266 and at startup: it stops the session, moves its file into `port_trace/` beside the port watch's database as `trace-<UTC stamp>.etl`, starts the session again and keeps the newest five saves (`-Keep`). It writes `port_trace/armed.json`, the marker the hub reads, and prints the folder. Disarm with the same command and `-Disarm`: it stops the session, removes the task and the marker, and keeps the saved traces. Neither touches a port range, `TcpTimedWaitDelay` or any other machine setting.

When a **new** `4231` or `4266` is read, the watch looks for the first saved trace stamped at or after the event and counts, per process, the AFD socket creates (event 1000), binds (1029, 1030) and closes (1001) and the TCPIP port reservations (1052, 1054; 1053 failed) in the `trace_window_s` (5 s) before it, with one `wevtutil qe <file> /lf:true` query filtered on event id and time, so only those events are read (`src/health/port_trace.py`). It stores the `trace` on the event, beside its snapshot and peak, and the event text ends with it: `trace, 5 s before the event: python.exe (local-llm-hub: -m pytest) (16,010 binds, 15,990 closes)`. The state says what the trace could tell:

| `trace.state` | Event text |
| --- | --- |
| `ok` | The top three processes by trace events, with their counts; no events reads `not confirmed (the circular trace may have wrapped past it)`, and a read capped at `trace_max_events` (200,000) says so |
| `not_armed` | `trace not armed`: no marker, or the trace was armed after the event |
| `pending` | The save after the event has not landed yet; re-read on each event-log read for `trace_wait_s` (600 s) |
| `not_saved` | Armed, but no saved trace covers the event after `trace_wait_s` |
| `failed` | The saved trace could not be read |

Every state but an `ok` read that found events says the sub-second holder is **not confirmed**; none of them reads "no holder". A trace event's process id is the process that made the call, so a holder that has exited since reads `pid N`. `ports.last_event` carries the `trace` and a `trace_summary`.

### Holder labels

Every fleet repo runs `python.exe`, so a name alone attributes nothing. The first time the window or a sample sees an interpreter (`python.exe`, `pythonw.exe`, `node.exe`) it records a `label`: the folder name of the git repo its working directory sits in (a `.git` folder or a worktree's `.git` file), then the module or script it runs, as in `local-llm-hub: -m pytest` (`src/health/port_label.py`). Only the repo folder's name and the module or script basename are kept: never a path, an argument or inline code (`-c` is named, its text is not). A process that is not an interpreter, or that exited or denied access before it could be read, has `label` `null`, which says the label is unknown, not that there is no repo. The lookup runs once per pid while it stays in the window or the sample, never per tally. The label shows beside the name in the peak and snapshot text, and on `peak.holders`, `snapshot.holders` and `top_holders` rows.

### Findings

| Metric | Default watch / act | Judged on |
| --- | --- | --- |
| `tcp_percent`, `udp_percent` | 50 / 80 | The worst sample in the last `hold_minutes` (15) |
| `tcp_time_wait` | 4,000 / 10,000 | The same; the reason names the top TIME_WAIT groups of the peak sample when it carries them |
| `tcpip_4231`, `tcpip_4266` | always `act` | For `event_hold_hours` (1) after the event, with its time |
| `tcpip_4227` | `watch`; `act` when a sample within `hold_minutes` of the event had `tcp_percent` or `tcp_time_wait` at its `act` line | The same hold, with the event's time |

Judging on the worst recent sample keeps a burst that lasted one tick at `act` for whoever looks a few minutes later; the reason then says when it peaked and where it is now (`TCP dynamic ports peaked at 85% in use at 14:02 (now 3% in use), mostly app.exe (8 k ports)`). When one process holds at least `attribution_share` of the ports in use, the fix is restarting it; otherwise it points at the runbook. A failed table read or event-log read is an `unknown` finding, and a port sample older than `stale_after_intervals` × `interval_s` makes ports `unknown`, never fine.

The act and cleared alerts are the health watch's own: each port tick re-judges the verdict, so an `act` from a burst is logged within one port interval rather than at the next 10-minute sample.

### The `ports` block

`GET /admin/api/machines/health` carries `ports`: `tcp` and `udp` each with `percent`, `used`, `total`, `range`, `worst_hour_percent` and `worst_hour_at` (TCP adds `time_wait` and `worst_hour_time_wait`); `top_holders` (name, pid, endpoints held, TCP / UDP split, growth per hour over the last hour, `null` until five minutes of history); `last_event` (the newest stored exhaustion event, kept for `event_keep_days` whether or not it still holds the verdict); `coverage`, `stale`, `sampled_at`, `sample_ms`, `cpu_ms`, `samples`, `interval_s`. `offenders.ports` repeats `top_holders`. The block is absent until the port watch has started.

### History

A sample whose TIME_WAIT reaches `wave_time_wait` (1,500; about 400 at rest) also keeps its TIME_WAIT sockets grouped by the server port they point at and that port's listener (`time_wait_groups`, as in the snapshot), so a wave of churn that never logs an event still names the port it hit; a calmer sample carries no groups. Samples are kept for `history_hours` (24) and events for `event_keep_days` (30) in SQLite under the runtime-data root (`port_watch.sqlite3`; override with `LLM_HUB_PORT_WATCH_DB_PATH`) and reloaded at hub start, so the incident before a restart or a reboot is still judged and shown.

### Cost

Measured on the tower (about 760 TCP and 160 UDP endpoints): a port sample is one `psutil.net_connections` call per protocol, **about 5 ms of CPU** every 30 s; the event-log read is **about 75 ms** of wall time for a 24-hour window, once a minute. Each sample's own cost is in `sample_ms` and `cpu_ms`. A window tally reads the same two tables: **about 10 ms median, 13 ms p95** of wall time (92 processes holding endpoints), roughly 1 % of one core at the armed 1 s interval and a tenth of that at the idle 10 s, in its own thread.

### Configuration

The `ports` section of `config/health_watch.json`, re-read every tick. Every threshold and cadence above is a key there; `ports.window_s`, `window_interval_s`, `window_idle_interval_s`, `window_arm_percent` and `window_confirm_percent` set the burst window's length, armed and idle tally intervals, the port use that arms it and the share of the range a rise must reach to confirm a holder; `wave_time_wait` is the TIME_WAIT a sample must reach to keep its groups; `trace_window_s`, `trace_wait_s` and `trace_max_events` set the seconds of trace read before an event, how long a save may take to land and the cap on the events one read counts. `"ports": {"enabled": false}` stops the port watch and the slow sample's port rows judge ports again.

## Resource watch: RAM, free space and disk latency

A second, faster watch answers a plainer question: **is this machine out of RAM or disk space, or is a disk stalling?** It samples every minute, keeps 14 days of history, sends a Telegram line when a condition is sustained, and counts stall episodes per drive for a weekly summary. The slow disk is the reason it exists: the tower's `E:` is an SMR hard disk holding the whole fleet, and a stall of about 1.7 s per read with a deep queue hangs jobs while the NVMe sits idle. The weekly count of those stalls is the measurement behind the decision to move the fleet's working tree onto the NVMe.

### What it reads

- **RAM**: free RAM as a percent of the total.
- **Free space** per fixed drive (`C:`, `D:`, `E:` on Windows; real mounts elsewhere), in GB.
- **Latency** per drive, from the Windows performance counters (`src/health/disk_counters.py`): `\PhysicalDisk(*)\Avg. Disk sec/Read` and `Avg. Disk sec/Write`, the same numbers `Get-Counter` and Resource Monitor show, with `Disk Reads/sec` and `Disk Writes/sec` for how many operations completed. One PDH query stays open and each sample closes one window, so the reading is the average time one operation took since the last sample, separately for reads and writes, and a queue of requests stuck behind a 1.7 s read shows up as 1.7 s. A drive letter is mapped to its `PhysicalDriveN` once with a metadata-only `IOCTL_STORAGE_GET_DEVICE_NUMBER`. psutil's per-disk `read_time` / `write_time` are not used: on Windows they arrive in whole seconds labelled as milliseconds, so every disk reads about 1000 times too fast and a busy NVMe reads 0. Off Windows latency is `unsupported`; RAM and free space work everywhere.
- **Load** per physical disk, from the same query: `% Idle Time` (reported as busy % = 100 minus idle) and `Current Disk Queue Length` (the requests waiting when the window closed). Drives that share a disk share one reading, labelled with every drive letter on it.
- **Credibility check.** A window in which a disk completed 50 or more operations in one direction yet reports an average of exactly 0 is `unknown` ("source not credible"), never `0 ms`. Every trusted latency row is tagged `src: "pdh"`; a stored row without that tag counts as unmeasured, so it lowers the weekly coverage instead of reading as "no stalls".

### Why sampling is cheap and never loads the slow disk

A sample is a handful of counter and metadata reads, about 0.3 ms for the performance-counter collection, and reads no file on any disk. The history is not written to the repo's drive: it lives in SQLite under the runtime-data root (`src/runtime_data.py`, `C:\sqlite\local-llm-hub\resource_watch.sqlite3` on Windows; override with `LLM_HUB_RESOURCE_WATCH_DB_PATH`), on the NVMe. One small row a minute is written there, never to `E:`.

### Alerts

Each condition must hold for several samples in a row. One odd sample never alerts.

| Condition | Default limit | Sustained for | Why this value |
| --- | --- | --- | --- |
| RAM free below | 10 % | 3 min | Below 10 % Windows is already trimming working sets, and the model servers hold tens of GB, so a dip is real pressure; the process watch uses the same `watch` line |
| A drive's free space below | 10 GB | 3 min | Enough room left to act (stop a download, free a cache) before writes fail; a percentage would read a 4 TB drive as fine at 40 GB |
| A disk's average latency above | 500 ms | 2 min | A healthy disk under load stays far below it; the stall that hung jobs ran at about 1,700 ms. Two minutes keeps a single long operation (a big copy finishing) from paging |
| A physical disk is overloaded: busy, queued and slow at once | watch at 90 % busy, queue 4, 30 ms per operation; alert at 95 % busy, queue 8, 100 ms | watch 3 min, alert 5 min | The measured starvation read 100 % busy, queue 14 to 34 and about 240 ms per read for several minutes, so the alert line sits well below it. A healthy disk here answers in a few ms. All three must hold: busy alone is a big sequential copy and a deep queue alone is an NVMe doing its job. A window in which requests are queued on a busy disk and none completed counts as slow |

Latency is judged only from windows in which a direction completed at least `min_ios` (5) operations: one slow read on an otherwise idle disk says nothing about a queue. The load rule does not use `min_ios`, because a busy disk with a deep queue is not idle.

The load rule has two levels. `watch` is shown on the Machines row and in the machine verdict but sends nothing; `alert` sends the Telegram line and raises the machine verdict to `act`, the same path as a port or commit alert.

Alerts go out through the fleet's one Telegram transport, `~/.claude/hooks/notify_send.py` (set `alert.script` to use another path), run as a subprocess with the hub's interpreter and the message on stdin. The destination is the transport's `attention` chat, resolved from fleet config; the hub holds no token and adds no recipient. Each condition (`ram`, `disk_free:E:`, `disk_latency:E:`, `disk_load:PhysicalDrive1`) sends **at most once per 6 hours**, counted from the last successful send and stored in the history database, so a hub restart does not repeat an alert still inside its window. A failed delivery is logged and recorded as `failed`, does not start the window, and is retried after 10 minutes. `alert.enabled: false` keeps the state and the summary but sends nothing.

### States

Every metric and the whole result is `ok`, `alert` or `unknown`; the disk load rule also has `watch` between `ok` and `alert`.

- `unknown` means the reading was not taken or cannot be trusted: a failed RAM or free-space read, an unmappable drive, a failed or unavailable performance-counter query, a window that fails the credibility check, the first sample after boot with no window yet, or a last sample older than three intervals. It is never folded into `ok`.
- `unsupported` (latency off Windows) is listed but does not hold the result at `unknown`.
- Alert outranks watch, watch outranks unknown, and unknown outranks ok.

### Weekly stall summary

A **stall episode** is a run of consecutive minutes, at least `disk_latency.sustain_samples` long, whose latency is above the limit. A minute that could not be read, or a gap while the hub was down, ends the run instead of bridging it. For each drive the summary reports the episode count, total minutes and worst latency over `summary_days` (7), and how much of the window was actually measured. A drive with no episodes and less than `min_coverage_percent` (50) of the window measured is `unknown` ("not confirmed"), not "no stalls": a quiet week only counts if the disk was watched. A young history says how many hours it has watched so far rather than claiming a week. A line looks like `E: 3 stalls, 41 min total, worst 1720 ms, the last 7 days (96% measured)`.

### Where it shows

- **`GET /admin/api/machines/resources`**: `state`, `reason`, the latest RAM and per-drive readings, every `conditions` entry, the `alerts` sent (each `sent` or `failed`), and `stalls` (`line` plus one entry per drive with `episodes`, `total_minutes`, `worst_ms`, `covered_percent`, `recent`). `store` is `failed` when the history could not be written, which makes the stall summary `unknown`. In memory, cheap to poll; it is what a fleet health check reads.
- **`GET /admin/api/machines/health`** (the machine verdict, read by the launcher's monitor): a `disk_load` finding for each disk that is `watch` (level `watch`) or `alert` (level `act`), an `unknown` one when a disk's load cannot be read or the resource sample is stale, and a `disks` list with one entry per physical disk (`drives`, `state`, `busy_percent`, `queue`, `read_ms`, `write_ms`). The compact form a peer's row uses (`card_fields`) carries the same `disks`.
- **Machines tab**: this machine's sheet has a **Resources** section with the state, a live busy / queue / latency line per physical disk and one stall line per drive, and a *resource watch*, *resource alert* or *resources not confirmed* chip on the row when it is not fine.

### Configuration

The `resource` section of `config/health_watch.json`, re-read every sample like the rest. Every limit above (the load rule's are the flat `disk_load` keys), `interval_s` (60), `history_days` (14), `summary_days` (7) and the `alert` block are keys there; `"resource": {"enabled": false}` stops it. To prove the path end to end, raise a limit past the current reading (for example `ram_free_percent.alert_below: 101`) and watch one line arrive, then no second one inside the window.

## Layout

| Module | Role |
| --- | --- |
| `src/health/win_probe.py` | Native Windows reads: process table, performance info, GUI objects, dynamic port range, drive-letter to physical-disk number |
| `src/health/sampler.py` | One sample: system counters, the top processes per metric, each probe's state |
| `src/health/classifier.py` | The verdict: a pure function of the history and the config |
| `src/health/settings.py` | Config loading and defaults |
| `src/health/watch.py` | The loop, the rolling history and the act/cleared alerts |
| `src/health/port_probe.py` | One port sample from the endpoint tables, the TIME_WAIT-attributing snapshot, and the Tcpip exhaustion events from the System log |
| `src/health/port_window.py` | The rolling 60 s window of per-second, per-process endpoint counts and its peak summary |
| `src/health/port_trace.py` | Reads the owner-armed socket trace saved after a 4231/4266: per-process creates, binds, closes and reservations in the seconds before it |
| `scripts/port_trace.ps1` | Owner-run, elevated: arms (`-Arm`) or disarms (`-Disarm`) the circular trace and its SYSTEM save task |
| `src/health/port_label.py` | A holder's repo and entry point (`local-llm-hub: -m pytest`), read once per interpreter pid |
| `src/health/port_rules.py` | Port findings, holders with growth, the `ports` block (pure) |
| `src/health/sample_store.py` | The JSON-row sample history both stores build on |
| `src/health/port_store.py` | Port sample and event history in SQLite |
| `src/health/port_watch.py` | The 30-second port loop, the burst window's armed or idle cadence, and their state |
| `src/health/resource_probe.py` | One resource sample: RAM, free space per drive, latency from counter deltas |
| `src/health/resource_rules.py` | Sustained-breach conditions (RAM, free space, latency, disk load), stall episodes, the weekly summary (pure) |
| `src/health/resource_store.py` | Rolling sample history and alert debounce state in SQLite |
| `src/health/resource_alert.py` | Telegram delivery through the fleet transport, and the per-condition debounce |
| `src/health/resource_watch.py` | The one-minute loop and the cached verdict |

## Scope

Each hub watches the machine it runs on. Peer verdicts on the Machines tab are not part of it yet. The process watch alerts to the hub log only; the resource watch alone pushes to Telegram.
