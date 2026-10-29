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

## Layout

| Module | Role |
| --- | --- |
| `src/health/win_probe.py` | Native Windows reads: process table, performance info, GUI objects, dynamic port range |
| `src/health/sampler.py` | One sample: system counters, the top processes per metric, each probe's state |
| `src/health/classifier.py` | The verdict: a pure function of the history and the config |
| `src/health/settings.py` | Config loading and defaults |
| `src/health/watch.py` | The loop, the rolling history and the act/cleared alerts |

## Scope

Each hub watches the machine it runs on. Peer verdicts on the Machines tab, and a push alert channel beyond the hub log, are not part of it yet.
