# Install, update and migrate the Local LLM Hub

The hub installs, updates and migrates the way any app in the toolbox does: clone it and run one `setup.bat`. An update is a pull plus a restart. A migration, onto a fresh clone on the same PC or on another one, is one `setup.bat` call. None of them resets or overwrites what a PC has configured. The installer is `src/installer.py`, with `src/installer_migrate.py`, `src/installer_declaration.py` and `src/installer_inplace.py` for migration (project-scaffolding's `docs/installer.md`); what they do for this app is declared in the `[install]` tables of `.fleet.toml`.

## Install

You need:

- Windows 10 or 11, `git`, and Python 3.11 or newer (the `py` launcher, or `python` on `PATH`).
- The agent CLIs for the models you enable on this host: `claude` (Claude Code) for any `claude-*` model, and `agy` (Antigravity) for any `gemini-*` model. Both are signed in once, by you. The checklist's **Model files and backends** row reports whether each is on `PATH`.
- `ffmpeg` on `PATH` on the host that receives browser-recorded audio uploads (wav and mp4 uploads never need it).

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
4. ends with two lists: the required items, each `ok`, `missing` or `unknown`, and the optional features, each `available`, `off` or `unknown` with what it unlocks.

Setup has succeeded when it ends with `Setup complete` and exits 0; an optional feature that is `off` never changes that.

`setup.bat` does not download model weights or backend binaries: they are gigabytes, and the hub has its own installer for them. The checklist row **Model files and backends** runs the hub's read-only check (`python -m src.install`, about half a minute) and stays `off` until the downloads are done:

```powershell
.\.venv\Scripts\python.exe -m src.install --fix
```

That reads `config/models.yaml`, works out which host row this PC is (the `LOCAL_LLM_HUB_HOST` variable, else the hostname, else the default row) and downloads only what that row enables: `llama.cpp` and `whisper.cpp` under `vendor/`, the GGUF weights under `models/`, and the TTS and ComfyUI pieces when a TTS or image role is enabled. It can also be run from **Settings › Health & install** in the admin webapp.

The rest is optional until you want the feature:

- **Admin bearer token:** nothing to do. The tray mints one on first boot when `auth_token` in `config/webapp_config.json` is empty.
- **Sign-in password:** `.\.venv\Scripts\python.exe -m src.password set` stores a hash in `config/app_password.json`.
- **Private addressing for other machines:** copy `config/machines.local.example.yaml` to `config/machines.local.yaml` and fill in your own values. Without it the hub boots normally and every peer-dependent feature stays inert. `setup.bat` never creates it, because a copy of the sample would name placeholder peers.
- **Cloudflare tunnel:** copy `config/cloudflared.sample.yml` to `config/cloudflared.yml` and fill it in. `setup.bat` never creates it, because the tray would start a tunnel from the placeholder values.
- **Environment overrides:** copy `.env.example` to `.env` for the host override, Langfuse keys and the like.
- **Start at log on:** put a shortcut to `tray.bat` in the Windows Startup folder (`shell:startup`). `setup.bat -Startup` does nothing for this app: it declares no log-on registration.

`.\setup.bat -WhatIf` lists every change it would make and makes none.

### Uninstall

1. Remove the tray's shortcut from the Startup folder (`shell:startup`).
2. Quit the tray from its menu. The hub and its model backends stop with it.
3. Remove what the hub started outside its folder, if you used it: the Langfuse and SearXNG containers (`stop_langfuse.bat`, `stop_searxng.bat`, which keep their volumes) and the Cloudflare tunnel's credentials in `%USERPROFILE%\.cloudflared`.
4. Delete the folder. Keep a copy of the files listed under "Migrate" first if you may want them back. The runtime-data folder `C:\sqlite\local-llm-hub` is outside it: delete it too only if you do not want the usage history.

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

The pull is a fast-forward only while the tracked tree is clean. The one tracked file the hub writes itself is `config/models.yaml`, which the admin's placement cards commit and push from the one host named by `hub.config_write_host`, so the tree stays clean.

## Migrate

What moves is declared in `[install.migrate]` of `.fleet.toml`. All of it is git-ignored, a path this PC does not have is skipped, a file already in the new copy is never overwritten, and no value is ever printed (secrets are compared by hash):

- **Secrets and sign-in:** `.env`, `config/webapp_config.json` (bearer token, password, tailnet allowlist, CORS origins), `config/app_password.json`.
- **This PC's addressing and tunnel:** `config/machines.local.yaml`, `config/cloudflared.yml`.
- **Vocabulary and voices:** `config/aliases.local.json`, `config/transcription_glossary.json`, `config/transcription_glossary.local.json`, `config/tts_voices/`.
- **Settings the admin writes:** `config/startup_profile.json`, `config/worker_pools.json`, `config/diagnostics_settings.json`, `config/diagnostics_storage.local.json`, `config/fleet_maintenance.json`, and `config/machine_specs.yaml`.
- **State in the repo:** `data/code_usage_history.json`, `data/code_token_history.json`, `data/usage_digest_state.json`, `data/telemetry/`.
- **Generated stacks:** `assets/deploy/docker/langfuse/.env`, `assets/deploy/docker/searxng/config/`.

What stays where it is:

- **The runtime-data folder** `C:\sqlite\local-llm-hub` (`FLEET_DATA_ROOT` or `LOCAL_LLM_HUB_DATA_DIR` moves it): the usage, diagnostics, port-watch, resource-watch and Mac-results databases, the failover record, the sign-in log and the restart status. A new copy on the same PC reads it as it is.
- **Elsewhere on the PC:** the Cloudflare tunnel's credentials file in `%USERPROFILE%\.cloudflared` (named by `credentials-file` in `config/cloudflared.yml`, an absolute path to fix if the user folder differs), and the SSH key `.env` names in `LOCAL_LLM_HUB_SSH_KEY`. The hub serves plain HTTP on `:8000`, so it has no certificate of its own.
- **Downloads:** `vendor/` and `models/`. They are not copied (see the steps only you can do).

**Reinstall in the same folder (the usual case).** The run stops every process that runs from the folder: the tray, the hub and the backends started from it, a whisper server started from `vendor/` included, which the voice transcriber shares. Run it from a plain terminal whose current folder is outside the app, never from a session or editor working inside it. Finish or remove any git worktree of this checkout first: a worktree points into the folder's `.git`, which the rename moves.

```powershell
cd <PARENT FOLDER>
.\local-llm-hub\setup.bat -InPlace -WhatIf    # read the backup name and every step; nothing changes
.\local-llm-hub\setup.bat -InPlace
```

It keeps the replaced copy as `local-llm-hub.bak-YYYYMMDD-N`, clones the remote's default branch, copies the files above, installs, runs `tray.bat --restart` and passes only when `http://127.0.0.1:8000/admin/api/version` serves the new clone's commit from a process in that clone. The path is unchanged, so the Startup shortcut and anything else that stores it need no edit.

**Beside it (another folder, or an old copy that is not a git clone):**

```powershell
git clone https://github.com/ferraroroberto/local-llm-hub.git <NEW FOLDER>
cd <NEW FOLDER>
.\setup.bat -MigrateFrom <OLD FOLDER> -StopOld
```

On the same PC `-StopOld` is required: two copies cannot both hold the tray's lock and `:8000`. To move to another PC, bring the old folder and the runtime-data folder across first (with the hub stopped, so the databases' `-wal` and `-shm` files travel with them), then run the same commands there.

Only you can do these steps:

1. **Bring the downloads back.** Until you do, the new copy serves the subscription models but no local one. Move `vendor\` and `models\` from the old copy (the backup) into the new one, each only while the new copy has no folder of that name. Then reinstall what lives in `.venv` (the TTS packages) and fetch anything still missing, and restart:

   ```powershell
   Move-Item <OLD FOLDER>\vendor <NEW FOLDER>\
   Move-Item <OLD FOLDER>\models <NEW FOLDER>\
   cd <NEW FOLDER>
   .\.venv\Scripts\python.exe -m src.install --fix
   .\tray.bat --restart
   ```

2. **Repoint** whatever stores the old path, after a `-MigrateFrom` into another folder: the run lists what still points at it.
3. **Check it works:** in **Settings › Health & install** the checks match what the old copy showed, and one real request through `/v1/messages` to a model you use answers.
4. **Fall back** if verify fails (the report names the step): stop the new copy, rename the new folder away and the backup to the original name, move `vendor\` and `models\` back if you had moved them, and start it.
5. **Retire the backup** once you are sure the new copy works, by deleting it yourself. Nothing here or in `setup.bat` deletes it, and an agent working through this playbook asks you before it deletes anything.
