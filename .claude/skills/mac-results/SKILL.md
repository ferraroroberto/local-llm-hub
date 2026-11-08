---
name: mac-results
description: Run one pass of the nightly Mac screenshot puller (python -m src.fleet.mac_results), so each undelivered run's images reach the owner's chat (or, from a launcher Job, its artifacts), and relay the per-run outcome lines. Scheduled daily by run-nightly.bat; safe to re-run any time.
---

Deliver the nightly Mac platform pass's screenshots. This file owns the job's
procedure; the CLI, its states and the key setup are in `docs/mac-results.md`.

`send_file.py` delivers into a live launcher-hosted session
(`APP_LAUNCHER_SESSION_ID`). The scheduled Job is not one: under a Job the CLI
ignores any inherited session id and copies each image it could not send into
the Job run's artifacts, reporting `not_confirmed` (see "The scheduled job" in
`docs/mac-results.md`). A session started by hand delivers to its own chat.

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
   `empty`, `pending`, `refused`, `not_confirmed`, `audit_filed`, `audit_unknown`, `ci_reported`, `ci_unknown`, `ci_refused`, `digest_sent`, `digest_not_confirmed`) or
   `nothing new to deliver`,
   and the `already delivered` summary. Do not paraphrase a state into a
   softer one, and do not add a host, user, address or key path to anything
   you say.
3. Finish with one line: `MAC-RESULTS: exit <code>`, using the CLI's own exit
   code (0 delivered, filed or waiting, 1 unreadable, refused, not confirmed, audit unknown or a night's CI result unknown,
   2 not configured).

## Rules

- Never retry within the run: a failed image is resent by the next scheduled
  pass, and only the images that never landed.
- Never edit `config/`, `.env`, the delivered record or the Mac. Read-only
  apart from the CLI's own staging, record and routing-audit filing and nightly-CI filing (`ci-red-main` and `flaky-test` issues, through `gh`).
- A non-zero exit is the result, not a problem to fix: report the CLI's
  reason and stop. The wrapper `run-nightly.bat` turns the CLI's exit code
  (written to `MAC_RESULTS_STATUS_FILE` by the CLI itself) into the job's
  exit code, so the Jobs tab shows the failure.
