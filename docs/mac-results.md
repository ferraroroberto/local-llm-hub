# Mac nightly results: pull and deliver

The nightly Mac platform pass (the `nightly-mac` workflow in `project-scaffolding`) writes iOS Simulator screenshots and WebKit failure images to a folder on the Mac runner host. That repo is public, so the images never leave the Mac by the workflow and the runner holds no delivery credential. This hub pulls them over SSH and sends them to the owner's chat.

## What one pass does

`python -m src.fleet.mac_results --host <machine id>` runs one pass and exits:

1. `list` over the read-only key: every run folder under `~/mac-nightly-results/<repo>/<run id>/` with its age and whether `READY` exists. `READY` is the Mac's complete-run marker (four `key=value` lines: `run`, `repo`, `e2e`, `smoke`).
2. Runs older than the pull window (`--window-days`, default 4, well under the Mac's 14-day prune), runs already recorded as delivered and runs without `READY` are set aside. A run without `READY` is reported as `pending`, never delivered.
3. For each remaining run: `files` lists its `*.png` entries, each is gated (below), copied with `get` and sent with fleet-config's `send_file.py`, one caption per image: `<repo> · run <id> · <leg> <outcome> · <n>/<total>`.
4. Each confirmed send is recorded as it lands; the run is recorded as delivered once every image of it is confirmed. A retry resends only the images that never landed.

The delivered record is the hub's own SQLite file (`mac_results.sqlite3`, in the fleet runtime-data directory for `local-llm-hub`; `MAC_RESULTS_DB_PATH` overrides the file). Nothing is written to the Mac.

`--dry-run` lists and reports what would be delivered, copying, sending and recording nothing. The exit code is 0 when every run met is delivered or legitimately waiting, 1 when the Mac could not be read or a run is `refused` or `not_confirmed`, and 2 when the host, key or `send_file.py` is not configured.

## States

Each condition is reported on its own line and never folded into "nothing to deliver":

| State | Meaning | Delivered? |
| --- | --- | --- |
| `delivered` | every image of the run confirmed by `SENT=` | yes |
| `empty` | `READY` and the Mac lists no png | yes (nothing to send) |
| `pending` | no `READY` yet, or left for the next pass by the per-pass run cap | no, retried |
| `refused` | a gate failed (reason shown) | no, retried; fails again until it ages out |
| `not_confirmed` | the copy failed, or the send printed `REFUSED=` or `UNAVAILABLE=` | no, retried |
| `unreachable` (pass level) | the Mac's listing could not be read | no |

Only a `SENT=` line counts as sent. A timeout is `UNAVAILABLE`: the launcher may have stored the file, so a retry can rarely duplicate an image, but it is never lost.

## The run folder is untrusted

Adopter code produced it. The hub:

- validates `<repo>` (plain name), `<run id>` (digits) and every image path (up to four plain components ending `.png`) before building any remote command, and the script validates them again;
- refuses the **whole run**, with the reason, when a `*.png` entry is a link or other non-regular file, has a name outside the pattern, is over 8 MiB, when the run has more than 20 images or more than 40 MiB of them;
- checks each copied file against its listed size and the PNG signature, so a partial or swapped copy is refused;
- never executes or interprets content, ignores every non-`.png` name (traces included), and never puts the Mac's stderr (which can carry an address or login) into a report.

The runner user's own code can edit that user's `~/.ssh` and the dispatcher, so the forced command does not protect the Mac from itself: it bounds what the hub's key can do if the key leaks, and the hub-side validation bounds what a tampered Mac can make the hub do.

## Forced-command key setup

The key is dedicated and read-only. Its dispatcher, [`mac/bin/hub-results-pull.sh`](../mac/bin/hub-results-pull.sh), allows exactly three verbs (`list`, `files <repo> <run id>`, `get <repo> <run id> <file>`), reads only under `~/mac-nightly-results`, writes nothing and refuses a link anywhere on a path.

On the hub box, generate a passphrase-less key outside the repo:

```sh
ssh-keygen -t ed25519 -N "" -C hub-results-pull -f ~/.ssh/local_llm_hub_results_ed25519
```

On the Mac, as the runner user, copy the script to `~/bin/hub-results-pull.sh` (mode 755) and add one line to `~/.ssh/authorized_keys` (mode 600), with the public key from the `.pub` file:

```text
restrict,command="$HOME/bin/hub-results-pull.sh" ssh-ed25519 AAAA...your-public-key... hub-results-pull
```

`restrict` turns off the pty, agent, X11 and port forwarding and `~/.ssh/rc`; `command=` pins every login of this key to the script. Re-copy the script when the repo's copy changes.

In the hub's `.env` (never committed), point the puller at the private key:

```text
LOCAL_LLM_HUB_RESULTS_SSH_KEY=<path to the private key>
```

The machine id given to `--host` is a row of `config/machines.local.yaml` with an `ssh_user` (the runner user) and an address, the same row the Machines tab uses. No address, login or key belongs in this repo, a log or a chat caption.

Check the restriction from the hub: `whoami`, `list; id` and a `get` with `..` in the file name must each answer `rejected command` or `bad file name`.

## The scheduled job

`send_file.py` posts into the chat of a **live launcher session** (it needs `APP_LAUNCHER_SESSION_ID`, which the launcher sets for the agent sessions it hosts) and only accepts files under that session's folder or `%TEMP%/claude`, which is where the pass stages each image and removes it afterwards. The hub process has no such session, and job scheduling is not the hub's, so the pass runs as a **headless agent job** like the frontier refresh: [`.claude/skills/mac-results/run-nightly.bat`](../.claude/skills/mac-results/run-nightly.bat) starts `claude -p "/mac-results"`, and the [skill](../.claude/skills/mac-results/SKILL.md) runs one foreground pass and relays the CLI's per-run lines. A pass run without a launcher session reports `UNAVAILABLE=no-launcher-session` per image and delivers nothing.

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
