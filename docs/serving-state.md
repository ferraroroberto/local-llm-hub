# Serving state

Each local model the hub manages is in exactly one declared **serving state**, derived in one place, [`src/process/serving_state.py`](../src/process/serving_state.py), and read the same way by every surface that shows or acts on it.

## Why it is declared

An *implicit state machine* has real modes (stopped, loading, ready, failed) that are never declared as one official state. Instead they are inferred from scattered flags, empty fields, status strings and timers, so the legal combinations live only in someone's head. Bugs hide there: two screens infer different answers from the same facts, and an impossible combination passes silently because nothing says it is impossible.

The hub's model lifecycle used to be one of these. A model's mode was inferred from a live `Popen` handle, an inherited PID, a port snapshot and a reachability probe, each read differently by the Models tab, the audio health route and the fleet placement summary. A model that was loading read as "idle" on one screen and "running" on another; a crash during load reappeared as "stopped"; a failed port snapshot read as a free port.

Declaring the states makes a bad transition fail loudly (a dead process with a live load record is `failed`, with a reason, never `stopped`) and lets a newcomer read the whole lifecycle from one module.

## The states

| State | Meaning |
| --- | --- |
| `stopped` | No process of ours, nothing on the port. |
| `loading` | Ours, spawned or inherited, not yet answering, still inside the load deadline (`LOAD_DEADLINE_S`, the same budget an on-demand first request waits). |
| `ready` | Ours and answering. |
| `adopted` | Someone else's process holds the port; the hub routes to it but did not start it. |
| `failed` | Ours died on its own, missed the load deadline, or stopped answering after it was ready. `serving_reason` says which. |
| `unknown` | The listening-port snapshot failed, so `stopped` cannot be told from `adopted`. Start stays disabled. |

`backend_process.serving()` gathers one observation (process alive, probe answered, port snapshot, exit code) and `serving_state.derive()` maps it to a state. A per-spawn `LoadRecord` is what tells "died on its own" from "stopped by the hub": `start` creates it, a deliberate `stop` clears it. A foreign sibling's server inherited on a shared port gets no record, so its exit is never reported as the hub's failure.

## Who reads it

- `GET /admin/api/models` adds `serving_state` and, for `failed` and `unknown`, `serving_reason` to each controllable row. The older `ownership` and `pid` fields are derived from the same observation and kept for older clients and peers.
- The Models tab ([`app_web/static/serving_state.js`](../app_web/static/serving_state.js)) uses it for the row chip, the running count, the Start/Stop verb and the on-demand label. A row from a peer on an older build, which has no `serving_state`, falls back to its `ownership`.
- `GET /v1/audio/health` reports each backend's `state` (`idle` for a stopped on-demand row). `idle` and `loading` stay out of the verdict; `failed` counts as down.
- The fleet placement summary lists only `ready` and `adopted` models as running, while the VRAM estimate still counts every live process.

A new reader calls `backend_process.serving()`; it never re-derives a mode from the process handle, the port or the probe.

## Finding the next one

These are the signs of an implicit state machine:

- lifecycle-named flags (`loading`, `ready`, `started`, `pending`) that no single type owns;
- guards for the same concept that differ from one screen or route to the next;
- the variables for one concept spread across modules, each read on its own.

Finding them takes two passes. A deterministic scan runs all the time and flags suspects from those signs. An agent deep-dive then confirms each suspect against the code and the live behaviour before anything is declared.

Without guardrails, vibe coding recreates these machines: each quick change adds one more flag or one more local guard. That is why the derivation lives in one function and readers are expected to call it.
