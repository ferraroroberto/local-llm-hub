# Running the hub

```bat
run_hub.bat                      :: FastAPI hub on :8000 (the admin
                                 :: webapp lives inside it at /admin)

:: Active rotation — one parameterized launcher, id from config/models.yaml
launchers\run_model.bat qwen35_4b          :: agentic_light  on :8088
launchers\run_model.bat gemma4_26b         :: agentic_heavy  on :8087
launchers\run_model.bat whisper            :: audio_transcribe on :8090
launchers\run_model.bat whisper_translate  :: audio_translate on :8091 (eager CPU)
launchers\run_model.bat piper              :: audio_speech (piper) on :8096
launchers\run_model.bat orpheus            :: orpheus TTS on :8093 (on demand)
launchers\run_model.bat kokoro             :: kokoro TTS on :8095 (on demand)
launchers\run_model.bat chatterbox         :: chatterbox TTS on :8092 (on demand)
launchers\run_all.bat                      :: start every backend in `enabled:` for this host

:: Fallback / ad-hoc (still in `enabled:` on tower, not autostarted)
launchers\run_model.bat gemma4_e4b         :: previous agentic_light on :8086
launchers\run_model.bat qwen               :: cross-enabled for proxy only — see config/models.yaml for the owning host

:: Demoted candidates — present but not in `enabled:` by default
launchers\run_model.bat glm                :: llama-server for GLM on :8082
```

(macOS / Linux: `./run_hub.sh`, `./launchers/run_all.sh`, etc. For
boot-time start + keep-alive on a headless Linux satellite, install the
systemd unit at `linux/systemd/local-llm-hub.service` instead — see
[model-lifecycle.md](model-lifecycle.md).)

Once the hub is running, open `http://127.0.0.1:8000/admin/` for the
admin webapp — five tabs (Hub / Models / Play / Code / Machines) covering every operational concern. The
header gear opens Settings, where a Small / Default / Large text-size control
(persisted in the browser) scales every tab. Going to `http://127.0.0.1:8000/`
redirects there.

## Tray launcher (Windows)

```bat
tray.bat
```

Starts a resident system-tray icon (silent — no terminal window) that:

- Auto-starts the hub on :8000. Hub startup then brings up, for every launch
  surface (tray, `run_hub.bat`, or `python -m src.run_backend hub`):
  - the **registry-derived model set** — every `startup: eager` row in
    `config/models.yaml` whose preferred chain host is this machine
    (`model_registry.desired_model_ids`; see `config/models.yaml` for the
    current eager set on this host); `startup: on_demand` rows load on
    first request instead;
  - the **services** configured in **`config/startup_profile.json`**
    (gitignored live file; see the committed
    **[template](../config/startup_profile.example.json)**,
    services only): Docker + Langfuse if `docker` / `langfuse` are
    `true` (via the same `launch_stack()` the Services card's manual launch
    button uses), and AgentsView if `agentsview` is `true`. Toggle any service
    off from the admin SPA's **Settings › Service startup** (the gear), or hand-edit the
    JSON. Peer wake/sync is *not* a startup-profile toggle — it is owned by
    the fleet reconcile loop, driven by the registry-derived desired state.
    The Docker VM's memory is capped host-side by `%USERPROFILE%\.wslconfig`
    (`[wsl2] memory=16GB`) — see
    [Host prerequisite: WSL memory cap](telemetry-langfuse.md#host-prerequisite-wsl-memory-cap).
- Lets you toggle any other enabled local model on/off from the
  **🧠 Models** submenu (multiple may run concurrently).
- Surfaces the admin webapp via **🚀 Open admin** — same `:8000/admin/`
  URL, opened in your default browser. Live logs, per-backend counters,
  live request stream, sparklines, and a 🩺 health/install panel all
  live there.
- Surfaces the **local URL**, the **LAN URL**, and (when configured)
  the **Cloudflare tunnel URL** as one-tap clipboard copies — the
  Cloudflare one comes with `?token=<bearer>` appended, so a phone
  loading a fresh tab can hand it back to the SPA without typing.

Drop a shortcut to `tray.bat` in the Windows Startup folder
(`shell:startup`) so the box behaves as an always-on local-LLM
endpoint after login. Routine tray activity is silent; if the tray
ever crashes, a single-shot `tray-crash.log` is written at the repo
root with the traceback (delete it any time — it's only recreated on
the next crash).

**Safe restart.** `tray.bat --restart` reclaims the hub port **:8000** by
PID (scoped to this repo's `.venv`) and starts fresh; it deliberately
leaves `:8090` (whisper-server) and the llama-server model ports alone —
see [CLAUDE.md](../CLAUDE.md#internal-architecture) for the full reclaim
mechanics. One-line restart matrix:

| Touched | Restart |
| --- | --- |
| Hub code (`src/`, `app_web/`, `CLAUDE.md`-tracked config loaders) | `tray.bat --restart` |
| A model backend/TTS shim (llama-server row, whisper-server row, `src/tts_server.py`) | Stop/start that model from the Models tab or `launchers/run_model.bat <id>` — not a hub restart |

## Cloudflare tunnel (optional)

For phone-side access from outside the LAN, run cloudflared with a
named tunnel. The repo ships `webapp/cloudflared.sample.yml` — copy it
to `webapp/cloudflared.yml`, fill in your tunnel UUID + hostname, and
the tray will pick the hostname up automatically and surface
**📋 Copy Cloudflare URL** in its menu. The hub itself doesn't spawn
cloudflared (you own its lifecycle); the tray only *reads* the config.

The admin webapp's bearer token is generated on first tray boot and
persisted to `config/webapp_config.json`. Loopback callers bypass it;
anyone reaching the hub over the tunnel must present
`Authorization: Bearer <token>` or `?token=…` on the URL.

A missing `config/webapp_config.json` means "nothing configured yet"
(defaults). An **unreadable or malformed** one is a different state and
is never treated as defaults: the hub and `/admin` admit loopback
callers only — no token and no `extra_allowlist`, so Tailscale peers are
refused too — `/admin/api/login` and the passkey endpoints answer 503
`webapp config could not be loaded`, and the tray leaves the file
untouched instead of minting a token over it. Each surface logs a
`could not load webapp_config` warning. Fix the file, then restart the
tray/hub.

## Passkey (WebAuthn) gate — parked, server-side only (not planned)

`src/webauthn_gate.py` and `app_web/routers/webauthn.py` implement a
tested, working passkey (WebAuthn) second factor for `/admin` —
registration/authentication ceremonies, a device whitelist, session
tokens — but it is **deliberately unwired**: no enrollment button or
ceremony call anywhere in `app_web/static/`, and the session token
`finish_authentication` mints isn't checked by any request path.

Building that frontend piece is **not planned**:
`/admin` is only ever reached over loopback or Tailscale, and both
already bypass the bearer-token gate as fully trusted (loopback
outright, Tailscale via `extra_allowlist`) — there's no Cloudflare
tunnel exposure of `/admin` in practice, so a passkey second factor
has no remaining trust boundary left to protect. The code stays in
the tree untouched as a reference implementation in case that trust
model changes later; it isn't a live security feature today.

To poke at it directly anyway: set `webauthn_rp_id` / `webauthn_origin`
in `config/webapp_config.json` (needs the `webauthn` package from
`requirements.txt`; its absence just makes
`GET /admin/api/webauthn/status` report `available: false`), then
drive `POST /admin/api/webauthn/enroll/window` (loopback-only, opens
a 5-minute window) followed by the `/enroll/begin` →
`navigator.credentials.create()` → `/enroll/finish` ceremony from a
WebAuthn-capable browser tab — there's no built-in page for this, so
script it or drive it from devtools. Even fully enrolled, it won't
gate anything.

## Server adoption between launchers

The hub on :8000 (and each per-model port :808x) is single-owner — TCP
allows only one process to bind a port. To make `tray.bat`,
`run_hub.bat`, the parameterized `launchers/run_model.bat <id>`, and the
admin SPA's Hub/Models tabs coexist, every launcher follows the same
**adopt-or-spawn** rule:

- If the port is already reachable, the launcher *adopts* the running
  process (no second spawn, no error) and treats it as up.
- Each launcher only stops what it spawned itself. Closing the tray
  doesn't stop a hub that `run_hub.bat` started, and vice versa.
- The admin webapp's **🧠 Models** tab distinguishes managed vs.
  adopted processes and surfaces the foreign PID so you can decide
  whether to take over.

The admin webapp's **🛰 Hub** tab streams the hub log over SSE — even
for an adopted hub, since the log lines come from the *current*
process's in-memory ring rather than a captured stdout.

Equivalent Python entrypoints (run from the project root):

```bat
.venv\Scripts\python -m src.run_backend hub
.venv\Scripts\python -m src.run_backend qwen35_4b
.venv\Scripts\python -m src.run_backend gemma4_26b
.venv\Scripts\python -m src.run_backend whisper
.venv\Scripts\python -m src.run_backend whisper_translate
.venv\Scripts\python -m src.run_backend piper
.venv\Scripts\python -m src.run_backend chatterbox
.venv\Scripts\python -m src.run_backend orpheus

:: Fallback (still enabled, not autostarted)
.venv\Scripts\python -m src.run_backend gemma4_e4b
.venv\Scripts\python -m src.run_backend qwen   :: cross-enabled for proxy only — see config/models.yaml for the owning host

:: Demoted (ad-hoc only; not in tray autostart, not auto-installed)
.venv\Scripts\python -m src.run_backend glm
```

The hub binds on `0.0.0.0:8000`, so other machines on your LAN can
also reach it. The llama-server backends bind on loopback — they're
only reachable through the hub.

## LAN access

The hub binds on `0.0.0.0:8000`, so any machine on the same network
(another laptop, a VM, an agent like openclaw running next to you) can
use it.

1. **Start the hub** (either `run_hub.bat` / `.sh` at the repo root,
   or `tray.bat` on Windows — the tray autostarts the hub). Start any
   local backends you need from `launchers/run_model.bat <id>` or the
   admin webapp's **🧠 Models** tab.
2. **Find your LAN IP.** The admin webapp's **🛰 Hub** tab shows it
   as a clickable **LAN** link. From a terminal:

   ```bat
   ipconfig | findstr IPv4
   ```

3. **First run on Windows:** the firewall will prompt to allow Python
   through. Accept on **Private** networks only — never Public.
4. **Point the remote client at the LAN URL**, with the bearer token
   from `config/webapp_config.json` (loopback callers skip this; every
   non-loopback caller — including a LAN client — must present it):

   ```python
   from anthropic import Anthropic
   client = Anthropic(
       api_key="local-dummy",
       base_url="http://192.168.1.42:8000",   # your LAN IP here
       default_headers={"Authorization": "Bearer <token from config/webapp_config.json>"},
   )
   ```

**Security caveats.** The hub is **not** unauthenticated: loopback
callers bypass auth, but `ParentBearerTokenMiddleware`
(`app_web/middleware.py`) gates every non-loopback caller — including
`/v1/messages` and `/v1/chat/completions`, not just `/admin` — behind
the bearer token generated on first tray boot and persisted to
`config/webapp_config.json` (see *Cloudflare tunnel* above), unless the
caller's IP matches `extra_allowlist` in that same config (e.g. a
Tailscale peer). Anyone on the LAN who doesn't have the token still
can't reach it. That said, still only run this on trusted networks (home
LAN, office LAN you own): the token lives in a plaintext local file, and
a compromised LAN peer that reads it can spend your Claude quota and
burn your GPU. Do **not** port-forward the hub to the public internet,
and do not accept the firewall prompt on Public networks (cafés,
airports, hotel Wi-Fi).

