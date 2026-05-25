# CI e2e gate — Chromium-only

The `e2e` GitHub Actions workflow runs **Chromium only**. WebKit was dropped from the matrix in issue #24.

## Why

The `pull_request` trigger of the e2e workflow chronically flaked, even when the same commit SHA had already passed on the `push` trigger. PR #23 (closing #22) was admin-merged after five consecutive failed PR runs, each failing in a different place — the dominant failure mode was the WebKit step either hanging past timeout or racing on selector state. The push-trigger green run on the same SHA was the trustworthy signal, which meant the PR gate had stopped catching regressions and started taxing every merge.

The SPA at `/admin` has no Safari-specific code: there are no `-webkit-` JS APIs in use, no Safari quirks worked around, and the user's clients are Chromium-based. WebKit coverage was a nice-to-have, not load-bearing. Removing it eliminates the dominant failure mode at the cost of a class of regression that has never actually been caught in practice.

## What changed

- `.github/workflows/e2e.yml`: `python -m playwright install --with-deps chromium` (was `chromium webkit`); the `e2e — webkit` job step is removed.
- `scripts/verify-before-ship.ps1`: the `pytest (e2e · webkit)` step is removed. Local pre-ship runs Chromium only, matching CI.
- `requirements-dev.txt`, `README.md`, `tests/e2e/test_smoke.py`, `tests/e2e/test_code_usage_tab.py`: comments and docstrings updated to reflect Chromium-only.

## Options that were discarded

Two other options were considered in issue #24 and rejected.

**A — Fix it properly across the whole spectrum.** Add a `actions/cache` step keyed on the Playwright version, pin Playwright + browser revisions in `requirements.txt`, add `--reruns 2` to the WebKit invocation, audit `tests/e2e/conftest.py` for cold-start races on the hub fixture, add a `concurrency:` block to cancel superseded PR runs. This would have preserved WebKit coverage but cost ongoing maintenance for a coverage class the project does not need. Not worth it for a single-maintainer repo.

**C — Remove the CI gate entirely.** `scripts/verify-before-ship.ps1` runs the same checks locally and is fast on the maintainer's hardware. The CI gate's only job is to catch things the local gate missed, which has not been observed. Rejected because the Chromium gate has been useful (it has caught real regressions on the `push` trigger) — only WebKit was pathological. Keeping the chromium gate preserves the safety net cheaply.

## Revisit if

- Chromium-only also starts flaking on the runner image — at that point reconsider C (drop the gate) rather than A (try to make WebKit work).
- The SPA gains Safari-specific code (a PWA install flow, a `-webkit-` API, an iOS-only quirk worked around in `main.js`) — at that point WebKit coverage becomes load-bearing and option A is back on the table.
