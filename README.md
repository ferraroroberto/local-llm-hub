# Local LLM Hub

A tiny local HTTP hub that routes `POST /v1/messages` (Anthropic shape) and
`POST /v1/chat/completions` (OpenAI shape) to several backends by `model` name,
plus a local whisper.cpp ASR pair that clients hit directly.

## Active rotation

Five entries in active use as of the May 2026 frontier reading:

- **`claude-*`** — forwarded to the **`claude -p`** CLI on your machine,
  using your local Claude Code auth (your subscription) instead of an
  API key. Aliases `claude-haiku-4-5`, `claude-sonnet-4-6`,
  `claude-opus-4-7` all hit the same backend; the CLI picks the model.
- **`qwen3.5-4b`** — local `llama-server` running
  [unsloth/Qwen3.5-4B-GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF)
  on `127.0.0.1:8088` (4 B hybrid Gated DeltaNet + sparse MoE, full
  GPU offload, Apache 2.0, 262 k native context). Fills the
  `agentic_light` role: OpenClaw fast lane, classification, edge.
  Also addressable as `model="agentic_light"` — clients that hit the
  role alias survive future `/swap-model` rotations unchanged.
- **`gemma4-26b-a4b-it`** — local `llama-server` running
  [unsloth/gemma-4-26B-A4B-it-GGUF](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF)
  on `127.0.0.1:8087` (25 B / 3.8 B-active MoE, IQ4_XS i-matrix quant
  — whole model on GPU in 16 GB VRAM). Fills the `agentic_heavy` role:
  deep agentic, transcript polishing, document work, EN↔ES↔CA. Also
  addressable as `model="agentic_heavy"` for the same reason.
- **`whisper-large-v3-turbo`** — local `whisper-server`
  ([ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp))
  running [ggml-large-v3-turbo.bin](https://huggingface.co/ggerganov/whisper.cpp)
  on `127.0.0.1:8090`. OpenAI-compatible `/v1/audio/transcriptions`;
  clients POST to `:8090` directly (the hub does not proxy audio
  endpoints). Port 8090 is a shared mutual-exclusion lock with
  `E:\automation\automation\audio\transcribe_voice`. Fills the
  `audio_transcribe` role.
- **`whisper-medium-translate`** — sibling whisper-server on
  `127.0.0.1:8091` running `ggml-medium.bin` on CPU. Same
  OpenAI-compatible `/v1/audio/transcriptions` shape; supports
  `task=translate` (turbo is transcription-only — its decoder distill
  drops translation). Eager-loaded (~1.5 GB RAM, always ready). Fills
  the `audio_translate` role. A lazy-load mode is also available — see
  [src/whisper_translate_proxy.py](src/whisper_translate_proxy.py) — for
  hosts that need to reclaim RAM when translate is rare.

## Demoted candidates (kept defined, not in active rotation)

`qwen3.5-9b` and `glm-4.5-air` are **defined in `config/models.yaml`**
but not in any host's `enabled:` list anymore. Their launchers still
exist (`launchers/run_qwen.bat`, `launchers/run_glm.bat`) for ad-hoc
bring-up. Demoted on 2026-05-10 per the May 2026 frontier reading —
see [docs/changelog/20260510-frontier-via-slash-commands.md](docs/changelog/20260510-frontier-via-slash-commands.md)
for the reasoning.

`gemma4-e4b-it` is the previous `agentic_light` role-holder, replaced
by `qwen3.5-4b` on 2026-05-10 via `/swap-model`. It is **kept in
`enabled:`** on the reference host for ad-hoc bring-up via
`launchers/run_gemma4_e4b.bat`, but no longer autostarted.

## Roles & monthly refresh

The four active local roles live in `config/models.yaml` → `roles:`:

| Role | Model | Why |
|---|---|---|
| `agentic_light` | `qwen35_4b` | OpenClaw fast lane / classify / edge |
| `agentic_heavy` | `gemma4_26b` | Deep agentic, transcripts, docs, ES↔EN↔CA |
| `audio_transcribe` | `whisper` | EN/ES audio → text |
| `audio_translate` | `whisper_translate` | ES audio → English (eager CPU sibling) |

Two Claude Code slash commands drive the monthly refresh, both
human-in-the-loop, both edit files directly:

- **`/frontier-refresh`** — runs the research, regenerates
  `docs/frontier/runs/<today>/{report.md,frontier.json,frontier.html}`,
  repoints `LATEST`. **Read-only on the registry** — produces artifacts
  only, never rewires anything.
- **`/swap-model`** — interactive role swap. Reads the latest run +
  current roles, asks one question at a time (which role, which target,
  hf_repo if not registered, download now?), shows the planned diff,
  then edits `config/models.yaml` + writes a launcher pair + (optionally)
  shells out to `scripts/download_models.py`.

The Streamlit **🛰 Frontier** tab is read-only: run picker, current
role decisions, report markdown, embedded chart. To act on a run, run
`/swap-model` from the CLI.

Side-by-side technical specs + docs links for all active models live in
[docs/model-comparison.md](docs/model-comparison.md). The latest research
brief and run sit under [docs/frontier/](docs/frontier/).

Point any client — the official `anthropic` or `openai` SDKs, openClaw,
a curl one-liner — at `http://127.0.0.1:8000` and swap backends by
changing the `model` string. Claude requests bill your subscription;
local model requests never leave the machine.

Inspired by the `_call_claude` pattern in
`E:\automation\inspiration-system\src\enrichment.py`.

## Latest-only policy

This repo intentionally ships **one model per role**. When a newer
release in the same family covers the same use case on the reference
hardware (e.g. Gemma 4 superseding Gemma 3), the older entry is
removed — registry, launchers, weights, and docs all go. The current
lineup and what each model is for live in
[docs/model-comparison.md](docs/model-comparison.md). Older entries
survive only in dated changelog notes under `docs/changelog/` for
historical context.

**Exception — one model per role, not per family.** The whisper
backend has two slots — `whisper` (turbo, 8090, transcription) and
`whisper_translate` (medium, 8091, translation). Turbo is
distill-decoded and was *not* trained on the translate task, so we
keep medium in a sibling slot for the rare cases when translation is
needed. Medium runs eager on CPU (~1.5 GB RAM) so the first translate
call is instant. The single-slot rule still applies *per role* —
there is one transcription model and one translation model.

## Scope & usage policy

This is a **personal playground** for running your own experiments
against your own Claude Code subscription and your own local GPU on
devices you personally own. It is **not** a hosted service, a
multi-tenant proxy, or a way to share subscription access.

To stay clearly within Anthropic's terms, please use it only as
intended:

- ✅ **Do** use it locally to call Claude from your own scripts,
  agents, and tools on devices you personally own.
- ✅ **Do** use it on a trusted LAN to reach your own second machine
  or VM (e.g. a local agent runtime).
- ✅ **Do** route non-Claude traffic to the local qwen/glm backends as
  much as you like — those are your own weights on your own silicon.
- ❌ **Don't** share the endpoint with other people — for Claude, that
  would be sharing subscription access, which Anthropic's
  [Consumer Terms](https://www.anthropic.com/legal/consumer-terms)
  don't allow.
- ❌ **Don't** port-forward it to the public internet or host it
  behind a domain.
- ❌ **Don't** build a product, commercial service, or large automated
  pipeline on top of the Claude path — for anything beyond personal
  experimentation use the paid API, which the
  [Usage Policy](https://www.anthropic.com/legal/aup) and Commercial
  Terms are designed for.
- ❌ **Don't** hammer `claude -p` in tight loops; keep volume at
  human-in-the-loop speeds so you don't abuse the service or get
  rate-limited. The local backends are rate-limited only by your GPU.

If your use case goes beyond "me, tinkering on my own machine,"
switch to the Anthropic API for Claude. When in doubt, check
[anthropic.com/legal](https://www.anthropic.com/legal/) or email
`support@anthropic.com`. This repo is provided as-is, with no
guarantee that it complies with Anthropic's terms for any particular
use.

## Architecture at a glance

```
openClaw / anthropic SDK / openai SDK / curl
                    │
                    ▼  http://<lan>:8000
   ┌────────────── FastAPI hub (src/server.py) ───────────────┐
   │  route by `model`:                                       │
   │    claude-*               → call_claude()   (claude -p subprocess)  │
   │    qwen3.5-4b             → llama-server 127.0.0.1:8088             │
   │    gemma4-26b-a4b-it      → llama-server 127.0.0.1:8087             │
   │    whisper-large-v3-turbo → 400 "POST to :8090 directly" (audio)    │
   │    whisper-medium-translate → 400 "POST to :8091 directly" (audio)  │
   └──────────────────────────────────────────────────────────┘

audio clients  ──────►  whisper-server 127.0.0.1:8090           (turbo, transcribe, GPU)
audio clients  ──────►  whisper-server 127.0.0.1:8091           (medium, translate, CPU)
                          (both speak OpenAI-compatible /v1/audio/transcriptions;
                           hub does not proxy /v1/audio/* — clients hit them directly)

Demoted (defined in config/models.yaml, not in any host's enabled list):
  qwen3.5-9b, glm-4.5-air — bring up via launchers/run_qwen.bat / run_glm.bat
Replaced as agentic_light on 2026-05-10 (still enabled on pc-cuda for fallback):
  gemma4-e4b-it — bring up via launchers/run_gemma4_e4b.bat
```

See [docs/project-structure.md](docs/project-structure.md) for the full
mermaid diagrams (components, modules, request lifecycle),
[docs/changelog/20260420-hub-with-qwen-and-glm.md](docs/changelog/20260420-hub-with-qwen-and-glm.md)
for the original hub post-mortem, and
[docs/changelog/20260422-add-whisper-asr.md](docs/changelog/20260422-add-whisper-asr.md) for
how the whisper backend slotted in.

## Layout

```
local-llm-hub/
├── .venv/                    # local virtualenv (gitignored)
├── .claude/
│   └── commands/             # Claude Code slash commands (committed)
│       ├── frontier-refresh.md   # produces a monthly research run
│       ├── swap-model.md         # interactive role swap (yaml + launcher + download)
│       └── system-specs.md       # collect Windows hardware specs
├── requirements.txt
├── tray.bat                  # Windows-only system-tray launcher (silent)
├── run_hub.bat / .sh         # start the FastAPI hub on :8000
├── launch_app.bat / .sh      # Streamlit control panel
├── launchers/                # per-model backends (.bat + .sh)
│   ├── run_qwen.*               # demoted candidate; ad-hoc only
│   ├── run_glm.*                # demoted candidate; ad-hoc only
│   ├── run_qwen35_4b.*          # agentic_light role on :8088
│   ├── run_gemma4_e4b.*         # ex-agentic_light fallback on :8086 (still enabled, not autostarted)
│   ├── run_gemma4_26b.*         # agentic_heavy role on :8087
│   ├── run_whisper.*            # audio_transcribe role on :8090
│   ├── run_whisper_translate.*  # audio_translate role on :8091 (eager CPU)
│   └── run_all.*                # start everything enabled on this host
├── config/
│   └── models.yaml           # hosts + models + roles + tray autostart
├── src/
│   ├── server.py             # FastAPI hub (both shapes) + router
│   ├── landing.py            # HTML landing page served at GET /
│   ├── claude_cli.py         # subprocess wrapper around `claude -p`
│   ├── openai_upstream.py    # httpx client + SSE think-strip pipeline
│   ├── model_registry.py     # YAML loader (resolves display_name + aliases)
│   ├── host_profile.py       # pick active host row
│   ├── machine_specs.py      # parse config/machine_specs.yaml
│   ├── fit_estimator.py      # back-end for the 🧮 Fit tab (HF model fit)
│   ├── system_stats.py       # live RAM/GPU readings for the Server tab
│   ├── install.py            # first-run checks + --fix
│   ├── run_backend.py        # hub|qwen35_4b|gemma4_26b|whisper|… dispatcher
│   ├── server_process.py     # hub Popen + ownership / adopt-or-spawn
│   ├── backend_process.py    # per-model Popen (llama-server + whisper-server)
│   └── whisper_translate_proxy.py  # FastAPI shim for optional lazy-load mode (dormant; whisper_translate is eager)
├── tray/                     # Windows system-tray launcher (silent pythonw)
│   ├── app.py                #   pystray menu + tk event pump
│   ├── log_window.py         #   tk Notebook tailing hub + per-model logs
│   ├── config.py             #   reads tray: section from models.yaml
│   ├── icon.py               #   PIL hub glyph (no image file in repo)
│   └── single_instance.py    #   .tray.pid lock validated with psutil
├── app/                      # Streamlit UI
│   ├── app.py                #   page nav (Welcome / Install / Server / …)
│   └── views/                #   one module per tab
│       ├── welcome.py
│       ├── install.py
│       ├── server.py
│       ├── comparison.py
│       ├── models.py
│       ├── testing.py
│       ├── playground.py
│       └── frontier.py       # NEW — read-only viewer for monthly runs
├── scripts/
│   ├── smoke_test.py
│   ├── download_models.py    # huggingface_hub → models/
│   ├── detect_machine_specs.py   # populate config/machine_specs.yaml
│   ├── install_llama_cpp.py      # CUDA-Windows / Metal-macOS release
│   └── install_whisper_cpp.py    # whisper.cpp CUDA/Metal release → vendor/whisper.cpp/
├── tests/                    # test_server / test_router / test_model_registry /
│                             # test_install / test_streaming
├── vendor/
│   ├── llama.cpp/            # prebuilt llama-server binary (gitignored)
│   └── whisper.cpp/          # prebuilt whisper-server binary (gitignored)
├── models/                   # downloaded GGUFs (gitignored):
│                             #   Qwen3.5-4B (Q4_K_M), gemma-4-26B-A4B-it (IQ4_XS),
│                             #   gemma-4-E4B-it (fallback, still enabled),
│                             #   ggml-large-v3-turbo.bin (whisper turbo, transcribe),
│                             #   ggml-medium.bin (whisper medium, translate),
│                             #   plus any demoted candidates if brought up ad-hoc
└── docs/
    ├── project-structure.md
    ├── model-comparison.md
    ├── frontier/                 # monthly efficient-frontier research
    │   ├── RESEARCH_PROMPT.md    #   canonical brief; read by /frontier-refresh
    │   └── runs/
    │       ├── LATEST            #   flat file containing the latest run date
    │       └── <YYYY-MM-DD>/     #   one dir per run
    │           ├── report.md     #   didactic markdown report
    │           ├── frontier.json #   machine-readable run data
    │           └── frontier.html #   standalone interactive chart
    └── changelog/                # dated post-mortems / decision notes
        └── …                     # see ls docs/changelog/ for the current list
```

## Setup

One command does everything — deps, llama.cpp binary, GGUF downloads
for the models enabled for this host:

```bat
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m src.install --fix
```

The installer reads [config/models.yaml](config/models.yaml), figures
out which host row you are (by `LOCAL_LLM_HUB_HOST` env var, else
hostname match, else `default: true`), and only downloads what that
host's `enabled` list asks for. On the reference Windows PC that's
the active rotation: Qwen 3.5 4B (~2.6 GB), Gemma 4 26B-A4B IQ4_XS
(~13.4 GB), whisper-large-v3-turbo (~1.62 GB), whisper-medium for
translate (~1.5 GB), plus Gemma 4 E4B (~5 GB) kept as the
agentic_light fallback, plus the llama.cpp + whisper.cpp CUDA
binaries under `vendor/`. On the Mac mini it's Qwen only.

The demoted candidates (`qwen3.5-9b`, `glm-4.5-air`) are in the
registry but **not** in any host's `enabled:` list, so the installer
ignores them. To bring one up ad-hoc, add it to `enabled:` and re-run
`--fix`, or just download manually with
`python scripts/download_models.py --only qwen` and launch via
`launchers/run_qwen.bat`.

Plain check (no changes):

```bat
.venv\Scripts\python -m src.install
```

Or use the **Install** tab in the Streamlit UI — same checks, same
fixes, one button per row.

Requires the `claude` CLI on `PATH` (Claude Code) if any `claude-*`
model is enabled for your host.

### Machine specs (optional)

[config/machine_specs_example.yaml](config/machine_specs_example.yaml)
documents the hardware schema this hub uses to reason about local
model fit (VRAM, system RAM, GPU compute capability). To populate the
real file for your host, run the detection script:

```bat
.venv\Scripts\python scripts\detect_machine_specs.py
```

```bash
./.venv/bin/python scripts/detect_machine_specs.py
```

Or copy the example manually and edit it:

```bat
copy config\machine_specs_example.yaml config\machine_specs.yaml
```

`config/machine_specs.yaml` is gitignored. AI coding agents working in
this repo will read it (when present) to recommend model sizes and
quantizations that actually fit your hardware. Optional — the hub
itself runs fine without it.

## Run

```bat
run_hub.bat                      :: FastAPI hub on :8000
launch_app.bat                   :: Streamlit control panel

:: Active rotation
launchers\run_qwen35_4b.bat      :: agentic_light  on :8088
launchers\run_gemma4_26b.bat     :: agentic_heavy  on :8087
launchers\run_whisper.bat        :: audio_transcribe on :8090
launchers\run_whisper_translate.bat :: audio_translate on :8091 (eager CPU)
launchers\run_all.bat            :: start every backend in `enabled:` for this host

:: Fallback / ad-hoc (still in `enabled:` on pc-cuda, not autostarted)
launchers\run_gemma4_e4b.bat     :: previous agentic_light on :8086

:: Demoted candidates — present but not in `enabled:` by default
launchers\run_qwen.bat           :: llama-server for Qwen on :8081
launchers\run_glm.bat            :: llama-server for GLM on :8082
```

(macOS / Linux: `./run_hub.sh`, `./launch_app.sh`, `./launchers/run_all.sh`, etc.)

### Tray launcher (Windows)

```bat
tray.bat
```

Starts a resident system-tray icon (silent — no terminal window) that:

- Auto-starts the hub on :8000 and the models listed in
  `config/models.yaml` under `tray.autostart_models` (default
  `[qwen35_4b, whisper, whisper_translate]`). Set it to `[]` to skip
  model autostart, or change the list to any subset of enabled model
  ids.
- Lets you toggle any other enabled local model on/off from the
  **Models** submenu (multiple may run concurrently).
- Streams hub + per-model logs in a tk window via **Open log window**.
- Opens the Streamlit admin UI on demand via **Open Streamlit admin**.

Drop a shortcut to `tray.bat` in the Windows Startup folder
(`shell:startup`) so the box behaves as an always-on local-LLM
endpoint after login. Routine tray activity is silent; if the tray
ever crashes, a single-shot `tray-crash.log` is written at the repo
root with the traceback (delete it any time — it's only recreated on
the next crash).

### Server adoption between launchers

The hub on :8000 (and each per-model port :808x) is single-owner — TCP
allows only one process to bind a port. To make `tray.bat`,
`run_hub.bat`, the per-model `launchers/run_*.bat` scripts, and the
Streamlit Server/Models tabs coexist, every launcher follows the same
**adopt-or-spawn** rule:

- If the port is already reachable, the launcher *adopts* the running
  process (no second spawn, no error) and treats it as up.
- Each launcher only stops what it spawned itself. Closing the tray
  doesn't stop a hub that `run_hub.bat` started, and vice versa.
- The Streamlit Server/Models tabs distinguish managed vs. adopted
  processes and offer a **Stop external (PID xxx)** button when you
  explicitly want to reclaim a port.

One known limitation: **logs aren't available for adopted processes**.
Windows can't attach to another process's stdout after the fact, so
the in-process ring buffer in the tray's log window or the Streamlit
Server tab stays empty for an adopted hub. The launcher that actually
spawned the process still has its log; check there. If you need cross-
process log tail badly, the future fix is to add a small file or
HTTP tail endpoint to the hub itself — out of scope for now.

Equivalent Python entrypoints (run from the project root):

```bat
.venv\Scripts\python -m src.run_backend hub
.venv\Scripts\python -m src.run_backend qwen35_4b
.venv\Scripts\python -m src.run_backend gemma4_26b
.venv\Scripts\python -m src.run_backend whisper
.venv\Scripts\python -m src.run_backend whisper_translate

:: Fallback (still enabled, not autostarted)
.venv\Scripts\python -m src.run_backend gemma4_e4b

:: Demoted (ad-hoc only; not in tray autostart, not auto-installed)
.venv\Scripts\python -m src.run_backend qwen
.venv\Scripts\python -m src.run_backend glm
```

The hub binds on `0.0.0.0:8000`, so other machines on your LAN can
also reach it. The llama-server backends bind on loopback — they're
only reachable through the hub.

## LAN access

The hub binds on `0.0.0.0:8000`, so any machine on the same network
(another laptop, a VM, an agent like openclaw running next to you) can
use it.

1. **Start the hub** (either `run_hub.bat` / `.sh` at the repo root, or
   the Streamlit *Server* tab, or `tray.bat` on Windows). Start any
   local backends you need from `launchers/run_qwen.*` /
   `launchers/run_glm.*` or the *Models* tab.
2. **Find your LAN IP.** The Streamlit *Server* page shows it as a
   clickable **LAN** link. From a terminal:

   ```bat
   ipconfig | findstr IPv4
   ```

3. **First run on Windows:** the firewall will prompt to allow Python
   through. Accept on **Private** networks only — never Public.
4. **Point the remote client at the LAN URL:**

   ```python
   from anthropic import Anthropic
   client = Anthropic(
       api_key="local-dummy",
       base_url="http://192.168.1.42:8000",   # your LAN IP here
   )
   ```

**Security caveats.** There is no authentication — anyone who can reach
the port can spend your Claude quota and burn your GPU. Only run this
on trusted networks (home LAN, office LAN you own). Do **not**
port-forward it to the public internet, and do not accept the firewall
prompt on Public networks (cafés, airports, hotel Wi-Fi).

## Use it from Python

Anthropic SDK, any backend:

```python
from anthropic import Anthropic

client = Anthropic(api_key="local-dummy", base_url="http://127.0.0.1:8000")

# Claude via subscription
msg = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)

# agentic_light role — Qwen 3.5 4B (hybrid Gated DeltaNet + sparse MoE, full GPU)
msg = client.messages.create(
    model="qwen3.5-4b",
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)

# agentic_heavy role — Gemma 4 26B MoE (25 B / 3.8 B-active on GPU)
msg = client.messages.create(
    model="gemma4-26b-a4b-it",
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)
print(msg.content[0].text)

# Or address the role directly — survives future /swap-model rotations
# unchanged. `agentic_light` and `agentic_heavy` both work the same way.
msg = client.messages.create(
    model="agentic_light",
    max_tokens=128,
    messages=[{"role": "user", "content": "Hello"}],
)
```

> Demoted candidates (`qwen3.5-9b`, `glm-4.5-air`) work the same way
> if you've brought them up ad-hoc — pass their model name as
> `model=`. The hub will return 400 if their backend isn't reachable.

OpenAI SDK (get native tool calls via `llama-server --jinja`):

```python
from openai import OpenAI
client = OpenAI(api_key="local-dummy", base_url="http://127.0.0.1:8000/v1")
msg = client.chat.completions.create(
    model="gemma4-26b-a4b-it",
    messages=[{"role": "user", "content": "Hello"}],
)
print(msg.choices[0].message.content)
```

Raw HTTP:

```bash
curl -s http://127.0.0.1:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma4-26b-a4b-it","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}'
```

List enabled models:

```bash
curl -s http://127.0.0.1:8000/v1/models
```

Transcribe audio via whisper (direct to :8090 — the hub does not proxy
audio endpoints):

```bash
curl -s -F file=@clip.wav -F response_format=json \
  http://127.0.0.1:8090/v1/audio/transcriptions
```

Translate non-English audio to English via the translate slot
(direct to :8091; medium runs eager on CPU, ~1.5 GB RAM):

```bash
curl -s -F file=@spanish.wav -F task=translate \
  http://127.0.0.1:8091/v1/audio/transcriptions
```

Or with the OpenAI SDK:

```python
from openai import OpenAI
asr = OpenAI(api_key="local-dummy", base_url="http://127.0.0.1:8090/v1")
with open("clip.wav", "rb") as f:
    r = asr.audio.transcriptions.create(model="whisper-large-v3-turbo", file=f)
print(r.text)
```

## Test

Unit tests (fast, no real `claude` / GPU calls):

```bat
.venv\Scripts\python -m pytest -q
```

End-to-end smoke test (requires hub + whichever backends you care
about running):

```bat
.venv\Scripts\python scripts\smoke_test.py
```

It iterates every enabled model from the registry, skips backends
whose port isn't reachable, and reports per-model pass/fail.

## Limitations (intentional — lightweight)

- **Partial streaming.** `POST /v1/chat/completions` with
  `stream: true` is fully supported for local backends — the hub
  proxies llama-server's SSE through, scrubbing `<think>...</think>`
  blocks from reasoning models (qwen / glm) so OpenAI-shape clients
  see only the final answer. The Anthropic-shape `POST /v1/messages`
  still returns a single JSON object when `stream: true` (Anthropic
  event translation is on the backlog below).
- Multi-turn chats are flattened into a single prompt for `claude -p`.
  (The local backends handle multi-turn natively through llama-server.)
- Tool-use translation across Anthropic ↔ OpenAI shapes is not
  implemented for qwen/glm. OpenAI-shape callers get native tool calls
  from llama-server's `--jinja` templates; Anthropic-shape callers to
  qwen/glm are text-only for now. Claude tool use passes through
  unchanged.
- Images / documents / extended thinking blocks are dropped at the
  shape boundary.
- Token counts reflect what each backend reports in its response.

## Backlog for improvement

Ordered roughly by payoff for API parity / developer experience.

**High value — closes real compatibility gaps**

- **Streaming (SSE) on `/v1/messages`.** OpenAI-shape streaming on
  `/v1/chat/completions` already lands as of
  [docs/changelog/20260510-openai-streaming-and-think-strip.md](docs/changelog/20260510-openai-streaming-and-think-strip.md).
  Still missing: map `claude -p --output-format stream-json` and
  llama-server's native SSE onto the Anthropic event shape
  (`message_start`, `content_block_delta`, `message_delta`,
  `message_stop`) so `client.messages.stream(...)` works unchanged.
- **Tool-use round-trips for qwen/glm on the Anthropic shape.** Accept
  `tools` + emit `tool_use`/`tool_result` content blocks, translating
  against llama-server's OpenAI-function-calling output.
- **Anthropic-shaped error responses.** Match
  `{"type":"error","error":{"type":"invalid_request_error","message":"..."}}`
  with the right status codes, so the SDK's retry / typed-exception
  logic behaves as it would against the real API.
- **Auth + version headers.** Accept and echo `x-api-key`,
  `anthropic-version`, `anthropic-beta` so clients that inspect them
  aren't surprised.
- **`POST /v1/messages/count_tokens`.** Useful for cost estimation;
  could shell out to a dry-run or use a tokenizer locally.
- **Request IDs.** Add `request-id` / `x-request-id` and thread them
  into logs for traceability.
- **CORS.** Enable it so browser-based clients and local webapps can
  call the hub directly.
- **Image & document content blocks.** Decode base64 attachments to a
  per-request temp dir, pass via `--add-dir` to Claude, and to the
  appropriate multimodal llama-server builds for qwen-VL when we add
  them.

**Medium value — fidelity and ergonomics**

- **Faithful multi-turn via `claude -p --input-format stream-json`.**
  Preserves prior assistant turns as real assistant messages rather
  than flattening. Better cache reuse.
- **`stop_sequences`, `temperature`, `top_p`, `top_k` passthrough** to
  every backend. Some the CLI supports, others must be documented as
  no-ops.
- **Stop-reason mapping.** Normalize CLI + llama-server stop reasons
  onto the Anthropic enum.
- **Persistent sessions via `--resume`.** Optional `session_id` in
  request metadata → reuse a CLI session for stateful chat with
  proper prompt-cache hits.
- **Metadata passthrough.** Log `metadata.user_id` and tie to request
  IDs for per-user observability.
- **Concurrency / process pooling for Claude.** Each request spawns a
  subprocess (~1–2 s overhead). A small warm pool or `--resume` reuse
  cuts p50 latency significantly.
- **Extended thinking blocks** (`thinking: {type:"enabled", budget_tokens}`)
  on the Claude path.
- **MLX backend for the Mac mini.** llama.cpp-Metal works; MLX is
  30–50 % faster for dense 9 B. Add as a new `backend: "mlx"` entry in
  the registry with a sibling to `openai_upstream.py`.

**Low value — nice-to-have**

- **`/v1/messages/batches`** (batch API).
- **Prompt-cache-control honoring** on system/message blocks.
- **Rate-limit response headers** (`anthropic-ratelimit-*`).
- **Structured logging + `/metrics`** for Prometheus-style
  observability.
- **Auto-start enabled backends with the hub.** Deliberately not done
  today so a 60 GB RAM model doesn't load when someone only wants
  Claude.

## License

[MIT](LICENSE). Use it, fork it, break it — just keep the copyright
notice. Note that the license covers *this code* only; your use of the
underlying `claude` CLI is still governed by Anthropic's terms (see
[Scope & usage policy](#scope--usage-policy) above) and the model
weights follow their own licenses
([Gemma terms](https://ai.google.dev/gemma/terms),
[Whisper / OpenAI MIT](https://github.com/openai/whisper/blob/main/LICENSE),
plus [Qwen](https://huggingface.co/Qwen) /
[GLM](https://huggingface.co/zai-org) for the demoted candidates).
