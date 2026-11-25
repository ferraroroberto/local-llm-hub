# Install, update and move the Local LLM Hub

The hub moves to another Windows PC the way any app in the toolbox does: clone it and run one `setup.bat`. An update is a pull plus a restart. Neither ever resets or overwrites what a PC has configured. The installer is `src/installer.py` (project-scaffolding's `docs/installer.md`); what it does for this app is declared in the `[install]` table of `.fleet.toml`.

## What you need

- Windows 10 or 11, `git`, and Python 3.11 or newer (the `py` launcher, or `python` on `PATH`).
- The agent CLIs for the models you enable on this host: `claude` (Claude Code) for any `claude-*` model, and `agy` (Antigravity) for any `gemini-*` model. Both are signed in once, by you. The checklist's **Model files and backends** row reports whether each is on `PATH`.
- `ffmpeg` on `PATH` on the host that receives browser-recorded audio uploads (wav and mp4 uploads never need it).

## Fresh PC

```powershell
git clone https://github.com/ferraroroberto/local-llm-hub.git
cd local-llm-hub
.\setup.bat
.\tray.bat
```

`setup.bat` runs `src/installer.py`, which:

1. creates `.venv` when it is missing;
2. installs `requirements.txt`, and later reinstalls only when that file changed (its hash is kept inside `.venv`);
3. creates `config/webapp_config.json` from `config/webapp_config.sample.json` when it is missing;
4. ends with a checklist that marks each item `ok`, `missing` or `unknown`.

`setup.bat` deliberately does not download model weights or backend binaries: they are gigabytes, and the hub has its own installer for them. The checklist row **Model files and backends** runs the hub's read-only check (`python -m src.install`, about half a minute) and stays `missing` until the downloads are done:

```powershell
.\.venv\Scripts\python.exe -m src.install --fix
```

That reads `config/models.yaml`, works out which host row this PC is (the `LOCAL_LLM_HUB_HOST` variable, else the hostname, else the default row) and downloads only what that row enables: `llama.cpp` and `whisper.cpp` under `vendor/`, the GGUF weights under `models/`, and the TTS and ComfyUI pieces when a TTS or image role is enabled. It can also be run from **Settings › Health & install** in the admin webapp.

What the checklist will still ask for, all of it optional until you want the feature:

- **Admin bearer token:** nothing to do. The tray mints one on first boot when `auth_token` in `config/webapp_config.json` is empty.
- **Sign-in password:** `.\.venv\Scripts\python.exe -m src.password set` stores a hash in `config/app_password.json`.
- **Private addressing for other machines:** copy `config/machines.local.example.yaml` to `config/machines.local.yaml` and fill in your own values. Without it the hub boots normally and every peer-dependent feature stays inert. `setup.bat` never creates it, because a copy of the sample would name placeholder peers.
- **Cloudflare tunnel:** copy `config/cloudflared.sample.yml` to `config/cloudflared.yml` and fill it in. `setup.bat` never creates it, because the tray would start a tunnel from the placeholder values.
- **Environment overrides:** copy `.env.example` to `.env` for the host override, Langfuse keys and the like.
- **Start at log on:** put a shortcut to `tray.bat` in the Windows Startup folder (`shell:startup`). `setup.bat -Startup` does nothing for this app: it declares no log-on registration.

`.\setup.bat -WhatIf` lists every change it would make and makes none.

## Update

```powershell
git pull --ff-only
.\setup.bat
.\tray.bat --restart
```

`setup.bat` on a configured PC is safe, and it is how a new requirement or a new `webapp_config.json` key arrives:

- Settings file (`config/webapp_config.json`): an existing value is never changed. A key the sample has and the file lacks is added, after a timestamped backup beside it (`webapp_config.json.bak-<YYYYmmdd-HHMMSS>`, git-ignored), and the added key names are printed, never values. A file that does not parse is left alone and reported, and the run exits 1.
- Every other config is yours: the installer neither creates nor rewrites it.
- A second run reports `unchanged` and writes nothing.

`tray.bat --restart` stops only the hub on `:8000` and starts a fresh one. It leaves whisper on `:8090` and the llama-server ports alone. Confirm the new build at `http://127.0.0.1:8000/admin/api/version`: its `git_sha` is the one you pulled.

The pull is a fast-forward only while the tracked tree is clean. Two tracked files are written by the hub itself: `config/models.yaml`, which the admin's placement cards commit and push from the one host named by `hub.config_write_host`, and `config/transcription_glossary.json`, which the dictionary editor rewrites in place. An edit to the dictionary left uncommitted makes `git pull --ff-only` refuse; commit it or restore it first.

## Migrate from an existing install

Use this on a PC that already runs an older copy of the hub: one that may not be a git clone, and may live at another path. The old copy keeps running until the new one is verified, and it is deleted only when you say so.

Where the state lives decides what you copy:

- **Inside the folder** (everything below under "Copy these"): it travels with a file copy.
- **Under the runtime-data root**, outside the folder: `C:\sqlite\local-llm-hub` on Windows (`FLEET_DATA_ROOT` or `LOCAL_LLM_HUB_DATA_DIR` moves it). It holds the usage database, the diagnostics, port-watch, resource-watch and Mac-results databases, the failover record, the sign-in log and the restart status. The new copy on the same PC reads the same folder, so **there is nothing to copy**; only moving to a different PC needs it, and then with the hub stopped, copy the whole folder (the usage database has `-wal` and `-shm` files beside it that go with it).
- **Elsewhere on the PC**, never in the folder: the Cloudflare tunnel's credentials file in `%USERPROFILE%\.cloudflared` (named by `credentials-file` in `config/cloudflared.yml`, an absolute path to fix if the user folder differs), and the SSH key `.env` names in `LOCAL_LLM_HUB_SSH_KEY`. The hub serves plain HTTP on `:8000`, so it has no certificate of its own.

1. **Clone fresh beside it.** `git clone https://github.com/ferraroroberto/local-llm-hub.git <NEW FOLDER>` next to the old folder; never clone over it.
2. **Copy these across.** From the old folder into the new one, keeping their relative paths. They are all git-ignored, so nothing tracked is touched, and a path the old copy does not have is simply skipped. Never open them to check: the first three hold secrets.
   - secrets and sign-in: `.env`, `config/webapp_config.json` (bearer token, password, tailnet allowlist, CORS origins), `config/app_password.json` (the sign-in hash);
   - this PC's own addressing and tunnel: `config/machines.local.yaml`, `config/cloudflared.yml`;
   - vocabulary: `config/aliases.local.json`, `config/transcription_glossary.local.json`, and `config/transcription_glossary.json` if the dictionary editor changed it (it is tracked, so compare it with the clone's before overwriting);
   - settings the admin writes: `config/startup_profile.json` (service autostart), `config/worker_pools.json`, `config/diagnostics_settings.json`, `config/diagnostics_storage.local.json`, `config/fleet_maintenance.json`;
   - `config/tts_voices/` (voice-cloning reference clips), and `config/machine_specs.yaml` if present (`scripts/detect_machine_specs.py` regenerates it);
   - state in the repo: `data/code_usage_history.json`, `data/code_token_history.json`, `data/usage_digest_state.json` and `data/telemetry/`;
   - generated stacks: `assets/deploy/docker/langfuse/.env` and `assets/deploy/docker/searxng/config/`;
   - downloads that save a long fetch: `models/` (the GGUF weights) and `vendor/` (`llama.cpp`, `whisper.cpp`, `piper`, `comfyui`). Skip either and `src.install --fix` downloads it again.
3. **Top them up.** Run `.\setup.bat -WhatIf` in the new folder and read what it would change, then `.\setup.bat`. `config/webapp_config.json` gains only the keys it lacks; the values you copied stay as they were.
4. **Stop the old copy.** Quit its tray from the tray menu: the hub stops with it and takes its model backends down. Remove the old copy's shortcut from the Startup folder (`shell:startup`). Confirm `:8000` answers nothing before the next step; whisper on `:8090` is shared with the voice transcriber and may belong to it.
5. **Start the new one.** `.\tray.bat`, and put its shortcut in the Startup folder if it should start at log on.
6. **Verify it.** Open `http://127.0.0.1:8000/admin/`, check that `git_sha` at `http://127.0.0.1:8000/admin/api/version` is the clone's `HEAD`, and in **Settings › Health & install** that the checks match what the old copy showed. Send one real request through `/v1/messages` to a model you use. If anything is wrong, quit the new tray and start the old one again: it is untouched.
7. **Retire the old folder, only after you confirm.** Nothing in this playbook or in `setup.bat` deletes the old copy. When you are satisfied, rename it first (for example `<OLD FOLDER>.retired`) and run on the new copy for a while; delete it yourself once you are sure. An agent working through this playbook asks you before it deletes anything.

## Uninstall

1. Remove the tray's shortcut from the Startup folder (`shell:startup`).
2. Quit the tray from its menu. The hub and its model backends stop with it.
3. Remove what the hub started outside its folder, if you used it: the Langfuse and SearXNG containers (`stop_langfuse.bat`, `stop_searxng.bat`, which keep their volumes) and the Cloudflare tunnel's credentials in `%USERPROFILE%\.cloudflared`.
4. Delete the folder. Keep a copy of the files listed under "Migrate" first if you may want them back. The runtime-data folder `C:\sqlite\local-llm-hub` is outside it: delete it too only if you do not want the usage history.
