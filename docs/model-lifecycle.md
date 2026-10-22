# Model lifecycle

## On-demand model lifecycle: `startup` + `idle_unload_minutes`

A `models:` row may declare a lifecycle policy:

```yaml
gemma4_26b:
  startup: on_demand        # default: eager (always-on)
  idle_unload_minutes: 30   # only meaningful for on_demand; omit to disable
```

An **on-demand** model is never started eagerly — not by hub autostart, not
by the fleet reconcile loop, not by the failover engine. The **first request**
that routes to it (chat, `/v1/messages`, `/v1/audio/speech`,
`/v1/audio/transcriptions`/`/translations`) spawns
the backend via `src/on_demand.py` and waits for readiness, so the first
call pays the load (tens of seconds for a big GGUF) and everything after is
warm. After `idle_unload_minutes` with no requests (in-flight requests hold
the window open), the idle watchdog stops the backend, and it **stays down**
until the next request — a hand-stopped or idle-unloaded on-demand model is
never resurrected by the supervisor loops. Before an on-demand load the hub
checks the VRAM budget math (running `est_vram_mb` sum + the candidate vs
the host's `vram_mb` ceiling) and logs a loud warning on overcommit —
warning only, never a block: WDDM overcommit degrades transiently and idle
unload recovers it. `gemma4_26b` (the single heaviest local model, rarely
called) is on-demand, which keeps tower's VRAM free for the voice path. The
eager/on-demand split is also the fleet's desired-state source (see
*Fleet placement* in [multi-host.md](multi-host.md)), and the
out-of-rotation rows (`gemma4_e4b`, `chatterbox`, `kokoro`, `glm`) are
explicitly `on_demand` so the derived desired set matches what actually runs.

## Linux satellite lifecycle: systemd

A headless Linux satellite (`gaming`, later `openclaw`) has no
tray-equivalent and no LaunchAgent — a **systemd unit** fills that role,
the counterpart to `tray.bat` on Windows and the LaunchAgent on macOS. The
template lives at `linux/systemd/local-llm-hub.service`: it runs the
existing `run_hub.sh` under `Restart=always` and enables at boot with no
login required (`WantedBy=multi-user.target`).

The Linux peer has **full lifecycle parity** with the Mac:

- **`install.py` fix** — `python -m src.install` on a Linux box checks the
  systemd unit (`is-active`/`is-enabled`) and the GPU (`nvidia-smi`); `--fix`
  renders the template's two placeholders, writes it to
  `/etc/systemd/system/` via `sudo -n tee`, then `daemon-reload` +
  `enable --now` (the systemd analogue of `_fix_launchagent`). Passwordless
  sudo is a prerequisite (see `docs/machines.md`); `sudo -n` fails fast with a
  clear message if it is missing rather than hanging on a prompt.
- **Remote bootstrap/sync** — `linux/bin/hub-remote-ctl.sh` is the systemd
  counterpart to `mac/bin/hub-remote-ctl.sh`, wired to the **same
  forced-command SSH key** (see [multi-host.md](multi-host.md)). `sync` = `git pull --ff-only` +
  `./.venv/bin/python -m pip install -q -r requirements.txt` +
  `systemctl restart`; `bootstrap` = the same, tolerant of a
  dead/never-started unit (`restart`, falling back to `start`). So
  `POST /admin/api/hosts/gaming/{bootstrap,sync}` and the reconcile loop's
  wake→bootstrap chain work on any systemd satellite, not just the Mac.
- **Admin stop/restart** — `POST /admin/api/hub/{stop,restart}` drive
  `systemctl stop`/`restart` when the hub detects it is running under systemd
  (`INVOCATION_ID` set). This is necessary because `Restart=always` would
  respawn a bare self-SIGTERM — a *deliberate* stop must go through systemd,
  the same reason the Mac's stop goes through `launchctl bootout`.

**Forced-command `authorized_keys` line (Linux peer).** The dedicated
automation key's entry in the satellite's `~/.ssh/authorized_keys` pins the
command exactly as the Mac's does — the key can only run the dispatcher, never
a shell:

```
command="/home/<user>/local-llm-hub/linux/bin/hub-remote-ctl.sh",no-port-forwarding,no-x11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA... local-llm-hub-remote-ctl
```

(On `gaming` the checkout is at `/home/gaming/local-llm-hub`, so the path is
`/home/gaming/local-llm-hub/linux/bin/hub-remote-ctl.sh`.) The script assumes
the repo lives at `~/local-llm-hub` — the same convention the Mac dispatcher
uses.

