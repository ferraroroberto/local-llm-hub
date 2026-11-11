# Actions-runner state

A fleet machine's GitHub Actions runner is described by two readings that fail separately, and the combinations are derived in one module, [`src/fleet/runner_state.py`](../src/fleet/runner_state.py). The Machines card, the verb buttons and the post-verb re-read all read it; [`src/fleet/actions_runner.py`](../src/fleet/actions_runner.py) only gathers the readings.

## The states

| Reading | States |
| --- | --- |
| Service (`svc.sh status`) | `running`, `stopped`, `loaded` (registered but no process), `not_installed`, `unknown` |
| GitHub's view of the runner | `online`, `offline`, `not_registered`, `unknown` |
| Latest workflow run | `passed`, `failed`, `cancelled`, `running`, `queued`, `none`, `unknown` |
| Headline (both readings) | `online`, `busy`, `stopped`, `not_installed`, `mismatch`, `unknown` |

GitHub's own words (`status`, `conclusion`, `in_progress`) are translated once, in `github_state()` and `run_state()`. A source that could not be read is `unknown`; `headline()` never folds it into a fine state.

## The transition table

`allowed()` is the only code that says which verb a pair of readings permits: `start` from `stopped` or `loaded`, `install` from `not_installed`, `stop` from `running` or `loaded` unless a job is running or GitHub could not be read. A verb outside the table is refused with the reason `allowed()` gives (HTTP 409) and nothing is sent to the machine. `converged()` says whether a re-read matches what a verb was meant to do.

## Readers

The Machines card ([`app_web/static/machines_runner.js`](../app_web/static/machines_runner.js)) shows the server's `headline`, `service`, `github` and `last_run` states through label tables keyed by those names; it never re-derives a headline.
