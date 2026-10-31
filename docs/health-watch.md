# Machine health watch

A machine that runs for weeks without a reboot fails slowly: one process leaks handles or memory for days until the whole box is short of a resource. The health watch is the hub's early warning for that. Every hub samples its own machine on a slow cadence, keeps a rolling history, and answers one question: **is anything leaking or under pressure, and is restarting one process enough, or does the machine need a reboot?**

It is read-only. It never kills, restarts or closes a process and never reboots a machine; the fix it names is advice.

## Where it shows

- **Machines tab**, this machine's row and sheet: an exception chip (*needs action*, *health watch*, *health not confirmed*) and a **Health** section with the verdict, every finding and the suggested fix. A healthy machine carries no chip.
- **`GET /admin/api/machines/health`**: the full verdict for the launcher and other readers. It is served from memory, so it is cheap to poll.

| Field | Meaning |
| --- | --- |
| `verdict` | `ok`, `watch`, `act` or `unknown` |
| `reason` | The worst finding in one line, with a count of the rest |
| `fix` | The suggested fix for the worst level, a process restart preferred over a reboot; `null` when nothing is wrong |
| `findings` | Every finding: `level`, `kind` (`process`, `system`, `probe`), `metric`, `value`, `reason`, `fix`; process findings add `process`, `pid`, `rate_per_hour`, `eta_hours` |
| `offenders` | The top three processes per metric with their value and growth per hour |
| `system` | The latest system counters (commit, RAM, pools, handles, TIME_WAIT, ephemeral ports, disk, uptime) |
| `coverage` | Each probe's state: `ok`, `failed`, `denied`, `unsupported` |
| `alerts` | The last 20 act and cleared events |
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
| `disk_free_percent` (OS drive, low is bad) | 15 / 5 | Free space on the system disk |

A process in `reboot_only_processes` (`System`, `csrss.exe`, …) cannot be restarted on its own, so its fix is a reboot.

The defaults are calibrated on a real case on the tower: one desktop process grew about 90,000 handles an hour to 3.9 M in about 44 hours and pushed the system total to 4.56 M, with commit and RAM fine. The watch reads that as `act` within half an hour of the growth starting (the growth rule needs four samples), and the fix it names is restarting that process, never a reboot. Closing the process brought the total back to 620 k, which is `ok`.

## Alerts

The alert is the hub log, visible in the Hub tab's live log: one warning when the verdict enters `act` (`🚨 health watch: act: <reason>. Suggested: <fix>`) and one info line when it leaves (`✅ health watch: cleared`). The pair fires once per episode, however many samples stay at `act`, and both land in `alerts` for the endpoint's readers.

## Cost

The sampler runs in a worker thread every `interval_s` (600 s), never on the event loop. On Windows the whole process table comes from one `NtQuerySystemInformation` call and the system counters from one `GetPerformanceInfo` call; psutil's per-process reads took about 8 s for about 600 processes on the tower and are not used there. Measured on the tower (about 640 processes, 800 TCP connections): **about 30 ms per sample**, plus about 70 ms once per hub start to read the dynamic port range from `netsh`. Each sample's own time is in `sample_ms`. The history keeps only the top processes per metric for `history_hours`, about 150 samples.

## Configuration

`config/health_watch.json` (committed) holds every threshold and the cadence. It is re-read on every sample, so a retune needs no hub restart, and it is merged over the built-in defaults, so a partial file only overrides what it names. `"enabled": false` stops the watch; the Machines tab then shows nothing for it. `null` disables any single limit.

## Resource watch: RAM, free space and disk latency

A second, faster watch answers a plainer question: **is this machine out of RAM or disk space, or is a disk stalling?** It samples every minute, keeps 14 days of history, sends a Telegram line when a condition is sustained, and counts stall episodes per drive for a weekly summary. The slow disk is the reason it exists: the tower's `E:` is an SMR hard disk holding the whole fleet, and a stall of about 1.7 s per read with a deep queue hangs jobs while the NVMe sits idle. The weekly count of those stalls is the measurement behind the decision to move the fleet's working tree onto the NVMe.

### What it reads

- **RAM**: free RAM as a percent of the total.
- **Free space** per fixed drive (`C:`, `D:`, `E:` on Windows; real mounts elsewhere), in GB.
- **Latency** per drive, from the Windows performance counters (`src/health/disk_counters.py`): `\PhysicalDisk(*)\Avg. Disk sec/Read` and `Avg. Disk sec/Write`, the same numbers `Get-Counter` and Resource Monitor show, with `Disk Reads/sec` and `Disk Writes/sec` for how many operations completed. One PDH query stays open and each sample closes one window, so the reading is the average time one operation took since the last sample, separately for reads and writes, and a queue of requests stuck behind a 1.7 s read shows up as 1.7 s. A drive letter is mapped to its `PhysicalDriveN` once with a metadata-only `IOCTL_STORAGE_GET_DEVICE_NUMBER`. psutil's per-disk `read_time` / `write_time` are not used: on Windows they arrive in whole seconds labelled as milliseconds, so every disk reads about 1000 times too fast and a busy NVMe reads 0. Off Windows latency is `unsupported`; RAM and free space work everywhere.
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

Latency is judged only from windows in which a direction completed at least `min_ios` (5) operations: one slow read on an otherwise idle disk says nothing about a queue.

Alerts go out through the fleet's one Telegram transport, `~/.claude/hooks/notify_send.py` (set `alert.script` to use another path), run as a subprocess with the hub's interpreter and the message on stdin. The destination is the transport's `attention` chat, resolved from fleet config; the hub holds no token and adds no recipient. Each condition (`ram`, `disk_free:E:`, `disk_latency:E:`) sends **at most once per 6 hours**, counted from the last successful send and stored in the history database, so a hub restart does not repeat an alert still inside its window. A failed delivery is logged and recorded as `failed`, does not start the window, and is retried after 10 minutes. `alert.enabled: false` keeps the state and the summary but sends nothing.

### States

Every metric and the whole result is `ok`, `alert` or `unknown`.

- `unknown` means the reading was not taken or cannot be trusted: a failed RAM or free-space read, an unmappable drive, a failed or unavailable performance-counter query, a window that fails the credibility check, the first sample after boot with no window yet, or a last sample older than three intervals. It is never folded into `ok`.
- `unsupported` (latency off Windows) is listed but does not hold the result at `unknown`.
- Alert outranks unknown, and unknown outranks ok.

### Weekly stall summary

A **stall episode** is a run of consecutive minutes, at least `disk_latency.sustain_samples` long, whose latency is above the limit. A minute that could not be read, or a gap while the hub was down, ends the run instead of bridging it. For each drive the summary reports the episode count, total minutes and worst latency over `summary_days` (7), and how much of the window was actually measured. A drive with no episodes and less than `min_coverage_percent` (50) of the window measured is `unknown` ("not confirmed"), not "no stalls": a quiet week only counts if the disk was watched. A young history says how many hours it has watched so far rather than claiming a week. A line looks like `E: 3 stalls, 41 min total, worst 1720 ms, the last 7 days (96% measured)`.

### Where it shows

- **`GET /admin/api/machines/resources`**: `state`, `reason`, the latest RAM and per-drive readings, every `conditions` entry, the `alerts` sent (each `sent` or `failed`), and `stalls` (`line` plus one entry per drive with `episodes`, `total_minutes`, `worst_ms`, `covered_percent`, `recent`). `store` is `failed` when the history could not be written, which makes the stall summary `unknown`. In memory, cheap to poll; it is what a fleet health check reads.
- **Machines tab**: this machine's sheet has a **Resources** section with the state and one stall line per drive, and a *resource alert* or *resources not confirmed* chip on the row when it is not fine.

### Configuration

The `resource` section of `config/health_watch.json`, re-read every sample like the rest. Every limit above, `interval_s` (60), `history_days` (14), `summary_days` (7) and the `alert` block are keys there; `"resource": {"enabled": false}` stops it. To prove the path end to end, raise a limit past the current reading (for example `ram_free_percent.alert_below: 101`) and watch one line arrive, then no second one inside the window.

## Layout

| Module | Role |
| --- | --- |
| `src/health/win_probe.py` | Native Windows reads: process table, performance info, GUI objects, dynamic port range, drive-letter to physical-disk number |
| `src/health/sampler.py` | One sample: system counters, the top processes per metric, each probe's state |
| `src/health/classifier.py` | The verdict: a pure function of the history and the config |
| `src/health/settings.py` | Config loading and defaults |
| `src/health/watch.py` | The loop, the rolling history and the act/cleared alerts |
| `src/health/resource_probe.py` | One resource sample: RAM, free space per drive, latency from counter deltas |
| `src/health/resource_rules.py` | Sustained-breach conditions, stall episodes, the weekly summary (pure) |
| `src/health/resource_store.py` | Rolling sample history and alert debounce state in SQLite |
| `src/health/resource_alert.py` | Telegram delivery through the fleet transport, and the per-condition debounce |
| `src/health/resource_watch.py` | The one-minute loop and the cached verdict |

## Scope

Each hub watches the machine it runs on. Peer verdicts on the Machines tab are not part of it yet. The process watch alerts to the hub log only; the resource watch alone pushes to Telegram.
