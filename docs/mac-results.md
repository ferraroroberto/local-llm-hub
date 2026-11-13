# Mac nightly results: pull and deliver

The nightly Mac platform pass (the `nightly-mac` workflow in `project-scaffolding`) writes iOS Simulator screenshots, WebKit failure images and a per-leg result to a folder on the Mac runner host. That repo is public and the runner holds no GitHub or delivery credential, so this hub is the one place that acts on the results: it pulls the images over SSH and sends them to the owner's chat, turns a red `main` into a GitHub issue, keeps the flake ledger and sends the morning digest line.

## What one pass does

`python -m src.fleet.mac_results --host <machine id>` runs one pass and exits:

1. `list` over the read-only key: every run folder under `~/mac-nightly-results/<repo>/<run id>/` with its age and whether `READY` exists. `READY` is the Mac's complete-run marker (`key=value` lines: `run`, `repo`, `sha`, `result`, the legs `unit`, `e2e`, `smoke`, `flaky`, `audit`).
2. Runs older than the pull window (`--window-days`, default 4, well under the Mac's 14-day prune), runs already recorded as delivered and runs without `READY` are set aside. A run without `READY` is reported as `pending`, never delivered.
3. For each remaining run: `files` lists its `*.png` entries, each is gated (below), copied with `get` and sent with fleet-config's `send_file.py`, one caption per image: `<repo> · run <id> · <leg> <outcome> · <n>/<total>`.
4. Each confirmed send is recorded as it lands; the run is recorded as delivered once every image of it is confirmed. A retry resends only the images that never landed.
5. A run whose `READY` says `audit=miss` and that is not yet recorded as filed also has its `audit.json` copied with `audit` and handed to project-scaffolding's `scripts/routing_audit.py file --audit <audit.json>` (a sibling checkout; `--routing-audit` overrides the path). That tool keeps exactly one open routing issue in the adopter, comments only when the failing-node set changed, and ends on `FILE=none|created|commented|unchanged` or `FILE=unknown`. Filing is independent of the images: neither waits on the other. A `clean` run files nothing.

6. The **nightly-CI stage** (below) reads each ready night's result and acts on it. It runs only when the pass has `gh`, never under `--dry-run` (which only lists the nights it would report), and is independent of the images and the audit.

The delivered record is the hub's own SQLite file (`mac_results.sqlite3`, in the fleet runtime-data directory for `local-llm-hub`; `MAC_RESULTS_DB_PATH` overrides the file). Nothing is written to the Mac.

`--dry-run` lists and reports what would be delivered, copying, sending and recording nothing. The exit code is 0 when every run met is delivered or legitimately waiting, 1 when the Mac could not be read or a run is `refused`, `not_confirmed`, `audit_unknown`, `ci_unknown`, `ci_refused` or `digest_not_confirmed`, and 2 when the host, key or `send_file.py` is not configured.

## The nightly gate: issues, flake ledger, digest

For every ready night in the pull window that is not yet recorded as reported, oldest first (so a later green closes an earlier red, never the reverse), the stage runs the dispatcher's `ready` verb (the marker, every character outside a plain set turned into `_`) and re-validates each field here: the commit must be hex, `result` one of `green`/`red`, each leg one of `pass`/`flaky`/`fail`/`not-run`/`unknown`, and `run`/`repo` must name the folder the marker came from. A word outside its vocabulary reads as `unknown`, and a `green` night that also has a `fail` leg contradicts itself and reads as `unknown`.

- **Red:** keeps exactly one open issue per repo titled `main's nightly gate is red`, labelled `bug` and `ci-red-main` (the label is created with `--force`, so repeated creation is harmless). It names the SHA, the failing legs and the failing test node ids; a later red night comments only when the SHA or the failing set changed (a hidden marker holds a digest of both). The ids come from the retry's junit file (`unit-retry.xml`, `junit-retry.xml`: what still failed), else the first attempt's, through the dispatcher's allowlisted `text` verb. A file the Mac says is not there leaves the ids unnamed in the issue; a file the Mac could not be asked for makes the night `ci_unknown`.
- **Green:** comments on and closes every open `ci-red-main` issue of the repo; with none open it does nothing.
- **Flake ledger:** a leg with result `flaky` has its test ids read from `unit-flaky.txt` / `e2e-flaky.txt` and appended to a host-local ledger (`flaky_nights` in `mac_results.sqlite3`), one row per repo, night and test, so a re-read never counts a night twice. A test flaky on **3 distinct nights within 14 days** gets one `flaky-test` issue (labels `bug`, `flaky-test`) in its repo; an open issue carrying the test's hidden marker dedupes it. Rows older than 30 days are pruned.
- **Digest:** one line per pass that has newly reported nights, sent through `send_file.py` as a one-line text file whose caption is the line, e.g. `Nightly CI: 6 run, 5 green, 1 red (task-os), 2 flaky`. A night whose result is unknown is named (`1 unknown (repo)`) and never counted green. A digest that was not confirmed sent is retried on the next pass; under a launcher Job the file is kept in the Job's artifacts like an image.

Only a commit SHA, leg names, counts and test node ids reach GitHub, never a log, trace, screenshot, path or host name. Ids are adopter-produced, so one is shown only if it matches a conservative character set with no slash, backslash or backtick, at most 50 are shown, and the issue says how many were left out. The junit XML is parsed only after a DOCTYPE or entity declaration has been refused, and is never executed.

A night is recorded as reported only when every step for it was confirmed (the issue action, the flake ledger, any flaky filing), so an unconfirmed `gh` call, an unreadable file or a marker with no known result is retried by the next pass and reported on every pass until it ages out. The `owner/repo` slug uses `MAC_RESULTS_GH_OWNER` (default: the fleet owner's account) and `gh` must be logged in on the hub box.

## States

Each condition is reported on its own line and never folded into "nothing to deliver":

| State | Meaning | Delivered? |
| --- | --- | --- |
| `delivered` | every image of the run confirmed by `SENT=` | yes |
| `empty` | `READY` and the Mac lists no png | yes (nothing to send) |
| `pending` | no `READY` yet, or left for the next pass by the per-pass run cap | no, retried |
| `refused` | a gate failed (reason shown) | no, retried; fails again until it ages out |
| `not_confirmed` | the copy failed, the send printed `REFUSED=` or `UNAVAILABLE=`, or a launcher Job kept the images in its artifacts | no, retried |
| `audit_filed` | the filing tool confirmed `FILE=created`, `commented` or `unchanged`; recorded, so a repeat pass changes nothing | yes |
| `audit_unknown` | `READY` records `audit=unknown` (or no audit line, or `not run`), or the filing was not confirmed (`FILE=unknown`, `FILE=none` against a `miss`, a failed copy, no `routing_audit.py`) | no, retried; never read as clean |
| `ci_reported` | the night's `ci-red-main` action and flaky steps were confirmed; recorded | yes |
| `ci_unknown` | `READY` has no known result, or the marker, a junit/flaky file or a `gh` call could not be confirmed (including a Mac that predates the `ready` and `text` verbs) | no, retried; never read as green |
| `ci_refused` | `READY` names another run or repo than its folder | no, retried |
| `digest_sent` | the digest line was confirmed sent | yes |
| `digest_not_confirmed` | the digest send was refused, unavailable or kept in a Job's artifacts | no, retried |
| `legacy` | a folder that can never become measurable: its `READY` has no audit line (written before the audit existed), or it has no `READY` a day after its last write. Reported on its own line, never counted in the verdict; a READY run's images are still delivered | yes (images) / nothing to do |
| `unreachable` (pass level) | the Mac's listing could not be read | no |

Every pass ends with a `verdict:` line: `red` when something above is unknown, refused or not confirmed, `ok - old folders only` when the only noise is `legacy` folders (they age out of the window by themselves), plain `ok` otherwise. A missing setup never reaches it: that exits 2 with a `setup missing - ...` line before the Mac is read. A new run whose `READY` says `audit=unknown` or `not-run` is a real unmeasured night and stays `audit_unknown`; so does a `READY` listed by a dispatcher that predates the audit field, because the folder itself is not known to be old.

An `audit=unknown` run is reported on every pass until it leaves the pull window, because the night was never measured (the first run of a repo only records a baseline, a skipped e2e leg is unmeasured too). A `READY` from a dispatcher or workflow that predates the audit field reads the same way. Only a `SENT=` line counts as sent. A timeout is `UNAVAILABLE`: the launcher may have stored the file, so a retry can rarely duplicate an image, but it is never lost.

## The run folder is untrusted

Adopter code produced it. The hub:

- validates `<repo>` (plain name), `<run id>` (digits) and every image path (up to four plain components ending `.png`) before building any remote command, and the script validates them again;
- refuses the **whole run**, with the reason, when a `*.png` entry is a link or other non-regular file, has a name outside the pattern, is over 8 MiB, when the run has more than 20 images or more than 40 MiB of them;
- checks each copied file against its listed size and the PNG signature, so a partial or swapped copy is refused;
- takes `audit.json` only as a regular file under a 256 KiB cap that parses as a JSON object naming the same repo as its run folder (the filing tool derives the target repo from that field), and ignores a link or any other entry of that name; the filing tool itself re-checks the shape and strips control characters and backticks before anything reaches an issue;
- never executes or interprets content, ignores every non-`.png` name (traces included), and never puts the Mac's stderr (which can carry an address or login) into a report.

The runner user's own code can edit that user's `~/.ssh` and the dispatcher, so the forced command does not protect the Mac from itself: it bounds what the hub's key can do if the key leaks, and the hub-side validation bounds what a tampered Mac can make the hub do.

## Forced-command key setup

The key is dedicated and read-only. Its dispatcher, [`assets/deploy/mac/bin/hub-results-pull.sh`](../assets/deploy/mac/bin/hub-results-pull.sh), allows exactly six verbs (`list`, `files <repo> <run id>`, `get <repo> <run id> <file>`, `audit <repo> <run id>`, `ready <repo> <run id>` and `text <repo> <run id> <name>`, the last reading only the six junit and flaky-list names the workflow writes, under a 4 MiB cap), reads only under `~/mac-nightly-results`, writes nothing and refuses a link anywhere on a path.

On the hub box, generate a passphrase-less key outside the repo:

```sh
ssh-keygen -t ed25519 -N "" -C hub-results-pull -f ~/.ssh/local_llm_hub_results_ed25519
```

On the Mac, as the runner user, add one line to `~/.ssh/authorized_keys` (mode 600), with the public key from the `.pub` file (the script it points at is installed from the hub, below):

```text
restrict,command="$HOME/bin/hub-results-pull.sh" ssh-ed25519 AAAA...your-public-key... hub-results-pull
```

`restrict` turns off the pty, agent, X11 and port forwarding and `~/.ssh/rc`; `command=` pins every login of this key to the script. Install the script, and re-run the same command whenever the repo's copy changes, from the hub box:

```sh
.venv/Scripts/python.exe -m src.fleet.mac_results_install --host <machine id>
```

It compares the SHA-256 and mode of `~/bin/hub-results-pull.sh` on the Mac with the repo's `assets/deploy/mac/bin/hub-results-pull.sh` and, when they differ, writes the repo's copy with mode 755 (to a temporary name, then a rename, so a pull never meets a half-written script). The results key is read-only and cannot write, so this uses the hub user's own general passwordless SSH to the same machine row, the channel the Machines tab's power actions use. It ends on one state, each with its own exit code: `unchanged` (0, the Mac already holds the repo's bytes at mode 755), `installed` (0, it differed, was written and read back equal), `not_confirmed` (1, the Mac could not be read, the write failed or the read-back differs: nothing is claimed) and, with `--check` (compare only, writes nothing), `would_install` (3). A configuration problem (no host, no address or login) is exit 2. The output carries only the state, a hash prefix and the mode, never the Mac's stderr. Until the Mac has the `ready` and `text` verbs, every night reads `ci_unknown`.

In the hub's `.env` (never committed), point the puller at the private key:

```text
LOCAL_LLM_HUB_RESULTS_SSH_KEY=<path to the private key>
```

The machine id given to `--host` is a row of `config/machines.local.yaml` with an `ssh_user` (the runner user) and an address, the same row the Machines tab uses. No address, login or key belongs in this repo, a log or a chat caption.

Check the restriction from the hub: `whoami`, `list; id` and a `get` with `..` in the file name must each answer `rejected command` or `bad file name`.

## The scheduled job

`send_file.py` posts into the chat of a **live launcher session** (it needs `APP_LAUNCHER_SESSION_ID`, which the launcher sets for the agent sessions it hosts) or, with no session id, into the **outbox of the Job run** it runs under (it needs `APP_LAUNCHER_JOB_ID` and `APP_LAUNCHER_JOB_RUN_ID`, which the launcher's Job executor sets), and only accepts files under that session's folder or `%TEMP%/claude`, which is where the pass stages each image and removes it afterwards. The hub process has no such session, and job scheduling is not the hub's, so the pass runs as a **headless agent job** like the frontier refresh: [`.claude/skills/mac-results/run-nightly.bat`](../.claude/skills/mac-results/run-nightly.bat) starts `claude -p "/mac-results"`, and the [skill](../.claude/skills/mac-results/SKILL.md) runs one foreground pass and relays the CLI's per-run lines. A pass run by hand with neither a launcher session nor a Job identity reports `UNAVAILABLE=no-launcher-session` per image and delivers nothing.

**A Job is not a launcher session.** The launcher sets `APP_LAUNCHER_SESSION_ID` only for the agent sessions it hosts. A Job copies the launcher's own environment, so it can carry an id inherited from whatever started the launcher. That id is stale at best (`REFUSED=session_not_found`); at worst it names an unrelated live session, and the screenshots would land in that chat. So when `JOB_ARTIFACT_DIR` is set (the launcher sets it for every Job run), the CLI:

- runs `send_file.py` with `APP_LAUNCHER_SESSION_ID` removed and the Job's own identity (`APP_LAUNCHER_JOB_ID`, `APP_LAUNCHER_JOB_RUN_ID`) kept, so with no session id `send_file.py` posts to the launcher's **Job outbox** for this run and a Job never delivers through an inherited id;
- records an image as delivered only when `send_file.py` answers `SENT=`;
- copies each image that was not sent (no Job identity, or the outbox refused or was unreachable) into the Job run's artifact folder (`<repo>-<run id>-<nn>-<name>.png`), where the Jobs tab shows it;
- reports a run with any unsent image `not_confirmed` (nothing recorded as sent, so the next pass retries it) and prints one pass-level line: `not_confirmed: a launcher Job could not deliver to the chat (inherited session id ignored | no session id); <n> image(s) copied to this Job run's artifacts instead`. The per-run line carries the `REFUSED=` / `UNAVAILABLE=` reason `send_file.py` gave. The job exits 1.

A Job whose outbox accepts the images exits 0 and prints no such line.

Configuration, all in the gitignored `.env`: `MAC_RESULTS_HOST` (the machine id the pass uses when `--host` is not given, so no host name lives in a committed file) and `LOCAL_LLM_HUB_RESULTS_SSH_KEY`.

**Exit code.** A headless `claude -p` exits 0 whatever its pass reported, so the CLI also writes its own exit code to the file named by `MAC_RESULTS_STATUS_FILE` (the `.bat` sets it and deletes any stale copy first) and the `.bat` exits with that code: 0 delivered, waiting or nothing new; 1 Mac unreadable, a run refused or not confirmed; 2 host, key or `send_file.py` not configured. A missing or non-numeric status file is exit 3, "not confirmed", never a pass; if `claude` itself fails, its code is returned. The Jobs tab therefore shows a failed pass as failed, with the CLI's reason in the job log.

**Schedule.** The nightly workflow's cron is 02:00 UTC and the Mac is busy until about 05:00 local, so the job runs once after that window and once more later as the retry (a retry resends only what never landed, and prints `nothing new to deliver` when there is nothing). The entry belongs in the owner's `jobs.json`, in the shape of app-launcher's `jobs.sample.json`; the repo does not edit that file:

```json
{
  "id": "mac-results",
  "name": "Mac results (nightly pull)",
  "script_path": "<repo checkout>\.claude\skills\mac-results\run-nightly.bat",
  "args": "",
  "schedule": { "type": "daily", "at": "06:00" },
  "visible": true,
  "alert_on_failure": true,
  "cooldown_seconds": 3600
}
```

The retry is a second entry with its own `id` and `"at": "12:00"`. Both jobs need the hub's `.env` to be the checkout's own, so `script_path` points at the primary checkout.
