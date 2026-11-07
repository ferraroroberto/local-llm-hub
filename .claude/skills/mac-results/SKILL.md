---
name: mac-results
description: Run one pass of the nightly Mac screenshot puller (python -m src.fleet.mac_results), so each undelivered run's images reach the owner's chat from inside a launcher session, and relay the per-run outcome lines. Scheduled daily by run-nightly.bat; safe to re-run any time.
---

Deliver the nightly Mac platform pass's screenshots. This file owns the job's
procedure; the CLI, its states and the key setup are in `docs/mac-results.md`.

The pass has to run inside a launcher-hosted session because `send_file.py`
only delivers into a live one (`APP_LAUNCHER_SESSION_ID`). That is the only
reason this is an agent job and not a plain script job.

**Unattended constraint (fleet-config#314):** run the pass in the foreground
and wait for it. A headless `claude -p` session cannot be resumed, so never
background it.

## Steps

1. From the repo root, run exactly one pass (the host id comes from
   `MAC_RESULTS_HOST` in `.env`; do not pass or print it):

   ```
   PYTHONUTF8=1 PYTHONUNBUFFERED=1 ./.venv/Scripts/python.exe -m src.fleet.mac_results
   ```

   When this session was started by hand with a `--dry-run` request, add that
   flag and nothing else.
2. Relay the CLI's output lines verbatim: one line per run (`delivered`,
   `empty`, `pending`, `refused`, `not_confirmed`) or `nothing new to deliver`,
   and the `already delivered` summary. Do not paraphrase a state into a
   softer one, and do not add a host, user, address or key path to anything
   you say.
3. Finish with one line: `MAC-RESULTS: exit <code>`, using the CLI's own exit
   code (0 delivered or waiting, 1 unreadable, refused or not confirmed,
   2 not configured).

## Rules

- Never retry within the run: a failed image is resent by the next scheduled
  pass, and only the images that never landed.
- Never edit `config/`, `.env`, the delivered record or the Mac. Read-only
  apart from the CLI's own staging and record.
- A non-zero exit is the result, not a problem to fix: report the CLI's
  reason and stop. The wrapper `run-nightly.bat` turns the CLI's exit code
  (written to `MAC_RESULTS_STATUS_FILE` by the CLI itself) into the job's
  exit code, so the Jobs tab shows the failure.
